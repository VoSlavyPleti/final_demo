from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

import httpx
import openai
from deepagents import create_deep_agent
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)

import main
from llm import get_llm


REVIEW_PROMPT = """
Выполни skill `final-finding-review` для
`/outputs/working/analysis.json`, `/inputs/contract.txt` и
`/inputs/matrix.json`. Перепроверь deviation, missing и extra по полным текстам
и `main_idea`, затем запиши `/outputs/working/final-result.json`.
""".strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run only final finding selector against an analysis.v3 artifact."
    )
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def prepare_workspace(
    analysis: Path,
    contract: Path,
    matrix: Path,
    output: Path,
) -> tuple[Path, Path]:
    analysis = analysis.expanduser().resolve()
    if not analysis.is_file():
        raise FileNotFoundError(f"Analysis file does not exist: {analysis}")
    main.validate_analysis_artifact(analysis)
    contract = contract.expanduser().resolve()
    matrix = matrix.expanduser().resolve()
    if not contract.is_file():
        raise FileNotFoundError(f"Contract file does not exist: {contract}")
    if contract.suffix.lower() != ".txt":
        raise ValueError(f"Contract must be a .txt file: {contract}")
    if not matrix.is_file():
        raise FileNotFoundError(f"Matrix file does not exist: {matrix}")
    if matrix.suffix.lower() != ".json":
        raise ValueError(f"Matrix must be a .json file: {matrix}")
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise ValueError(f"Output must be a .json file: {output}")
    if output == analysis:
        raise ValueError("Output path must differ from analysis path")
    output.parent.mkdir(parents=True, exist_ok=True)

    workspace = Path(tempfile.mkdtemp(prefix="contract-review-selector-"))
    (workspace / "outputs" / "working").mkdir(parents=True)
    (workspace / "inputs").mkdir()
    shutil.copyfile(analysis, workspace / "outputs" / "working" / "analysis.json")
    shutil.copyfile(contract, workspace / "inputs" / "contract.txt")
    shutil.copyfile(matrix, workspace / "inputs" / "matrix.json")
    shutil.copytree(
        main.FINAL_REVIEW_SKILL_SOURCE,
        workspace / "skills" / main.FINAL_REVIEW_SKILL_SOURCE.name,
    )
    return workspace, output


def run_selector(workspace: Path) -> None:
    # This process runs one selector only. Disable the automatically injected
    # general-purpose subagent so the selector cannot delegate or start a
    # second analysis. Filesystem and shell tools remain unchanged.
    register_harness_profile(
        "openai",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )
    agent = create_deep_agent(
        name="final-finding-selector",
        model=get_llm(),
        system_prompt=main.FINAL_REVIEWER_SYSTEM_PROMPT,
        backend=main.build_backend(workspace),
        skills=["/skills/final-finding-review/"],
        subagents=[],
    )
    retryable_errors = (
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )
    retry_number = 0
    thread_id = uuid.uuid4().hex
    while True:
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": REVIEW_PROMPT}]},
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 10_000,
                    "callbacks": [main.CompactTraceHandler()],
                },
            )
            return
        except retryable_errors as exc:
            retry_number += 1
            delay_seconds = min(2**retry_number, 30)
            print(
                f"Transient failure ({type(exc).__name__}); retrying in "
                f"{delay_seconds}s [retry {retry_number}]",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay_seconds)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    workspace: Path | None = None
    try:
        workspace, output = prepare_workspace(
            args.analysis,
            args.contract,
            args.matrix,
            args.output,
        )
        run_selector(workspace)
        generated = workspace / "outputs" / "working" / "final-result.json"
        main.validate_conclusion_artifact(generated)
        shutil.copyfile(generated, output)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
    print(f"[total] finished in {time.perf_counter() - started:.1f}s", flush=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
