from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import httpx
import openai
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain_core.callbacks import BaseCallbackHandler

from llm import get_llm


PROJECT_ROOT = Path(__file__).resolve().parent
ANALYSIS_SKILL_SOURCE = PROJECT_ROOT / "skills" / "integrated-contract-analysis"
FINAL_REVIEW_SKILL_SOURCE = PROJECT_ROOT / "skills" / "final-finding-review"

ANALYSIS_SYSTEM_PROMPT = """
Ты — оркестратор двух последовательных задач. Не выполняй юридический анализ и
не загружай исходные документы или содержимое промежуточного JSON в свой
контекст.

1. Вызови `analyzer` для `/inputs/contract.txt` и `/inputs/matrix.json`.
   Назначь ему выход `/outputs/working/analysis.json`.
2. После подтверждения завершения analyzer вызови `final-reviewer` для этого
   analysis и тех же входов. Назначь выход
   `/outputs/working/final-result.json`.

Оба субагента работают в общем файловом backend. Не копируй и не толкуй их
артефакты. При ошибке любого этапа заверши работу сообщением об ошибке. При
успехе верни только путь `/outputs/working/final-result.json`.
""".strip()

RUN_PROMPT = """
Запусти analyzer, затем final-reviewer. Входы:
`/inputs/contract.txt` и `/inputs/matrix.json`. Промежуточный результат:
`/outputs/working/analysis.json`. Итог:
`/outputs/working/final-result.json`.
""".strip()

ANALYZER_SYSTEM_PROMPT = """
Выполни `/skills/integrated-contract-analysis/SKILL.md` для
`/inputs/contract.txt` и `/inputs/matrix.json`. Запиши полный валидный
`analysis.v3` в назначенный задачей путь
`/outputs/working/analysis.json`. Не возвращай анализ в сообщении. После
самопроверки верни только путь и подтверждение завершения.
""".strip()

FINAL_REVIEWER_SYSTEM_PROMPT = """
Выполни `/skills/final-finding-review/SKILL.md` для
`/outputs/working/analysis.json`, `/inputs/contract.txt` и
`/inputs/matrix.json`. Не изменяй analysis. Запиши валидный `conclusion.v1` в
`/outputs/working/final-result.json` и верни только этот путь.
""".strip()

FINAL_REVIEW_RECOVERY_PROMPT = """
Выполни skill `final-finding-review` для
`/outputs/working/analysis.json`, `/inputs/contract.txt` и
`/inputs/matrix.json`. Запиши валидный `conclusion.v1` с
`completion_status: "complete"` в
`/outputs/working/final-result.json`. Если файл уже существует, прочитай его и
исправь через `edit_file`; не создавай другой итоговый путь. Перед завершением
повторно прочитай итоговый JSON и проверь его по схеме skill.
""".strip()


class CompactTraceHandler(BaseCallbackHandler):
    """Emit timing events without prompt or tool payloads."""

    run_inline = True

    def __init__(self) -> None:
        self._started: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _name(serialized: dict | None, fallback: str) -> str:
        if not serialized:
            return fallback
        if serialized.get("name"):
            return str(serialized["name"])
        identifier = serialized.get("id")
        if isinstance(identifier, list) and identifier:
            return str(identifier[-1])
        return fallback

    @staticmethod
    def _emit(event: str, run_id, parent_run_id, **fields) -> None:
        payload = {
            "event": event,
            "timestamp": round(time.time(), 3),
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            **fields,
        }
        print("[trace] " + json.dumps(payload, ensure_ascii=True), flush=True)

    def _begin(self, kind: str, name: str, run_id, parent_run_id) -> None:
        key = str(run_id)
        with self._lock:
            if key in self._started:
                return
            self._started[key] = (kind, time.perf_counter())
        self._emit(f"{kind}_start", run_id, parent_run_id, name=name)

    def _finish(
        self, kind: str, run_id, parent_run_id, error: str | None = None
    ) -> None:
        key = str(run_id)
        with self._lock:
            started = self._started.pop(key, None)
        duration = time.perf_counter() - started[1] if started else None
        fields = {
            "duration_seconds": round(duration, 3) if duration is not None else None
        }
        if error is not None:
            fields["error_type"] = error
        suffix = "error" if error else "end"
        self._emit(f"{kind}_{suffix}", run_id, parent_run_id, **fields)

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        self._begin("model", self._name(serialized, "chat_model"), run_id, parent_run_id)

    def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        self._begin("model", self._name(serialized, "llm"), run_id, parent_run_id)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("model", run_id, parent_run_id)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("model", run_id, parent_run_id, type(error).__name__)

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        self._begin("tool", self._name(serialized, "tool"), run_id, parent_run_id)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("tool", run_id, parent_run_id)

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("tool", run_id, parent_run_id, type(error).__name__)


class WindowsPowerShellBackend(LocalShellBackend):
    """LocalShellBackend whose execute tool uses PowerShell on Windows."""

    _VIRTUAL_SHELL_PATH = re.compile(
        r"(?<![A-Za-z0-9_:])/(inputs|outputs|skills)"
        r"(?=(?:[/\\]|[\s'\"`;,)\]}])|$)"
    )
    @classmethod
    def _normalize_virtual_shell_paths(cls, command: str) -> str:
        return cls._VIRTUAL_SHELL_PATH.sub(r"\1", command)

    def _ripgrep_search(
        self, pattern: str, base_full: Path, include_glob: str | None
    ) -> dict[str, list[tuple[int, str]]] | None:
        if os.name != "nt":
            return super()._ripgrep_search(pattern, base_full, include_glob)

        command = ["rg", "--json", "-F"]
        if include_glob:
            command.extend(["--glob", include_glob])
        command.extend(["--", pattern, str(base_full)])
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            return None

        results: dict[str, list[tuple[int, str]]] = {}
        for raw_line in completed.stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data", {})
            matched_path = data.get("path", {}).get("text")
            line_number = data.get("line_number")
            if not matched_path or line_number is None:
                continue
            physical_path = Path(matched_path)
            if self.virtual_mode:
                try:
                    result_path = self._to_virtual_path(physical_path)
                except (ValueError, OSError, RuntimeError):
                    continue
            else:
                result_path = str(physical_path)
            line_text = data.get("lines", {}).get("text", "").rstrip("\r\n")
            results.setdefault(result_path, []).append((int(line_number), line_text))
        return results

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if os.name != "nt":
            return super().execute(command, timeout=timeout)
        if not isinstance(command, str) or not command.strip():
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        if timeout is not None and timeout < 0:
            raise ValueError(f"timeout must be non-negative, got {timeout}")
        process_timeout = None if timeout in (None, 0) else timeout
        command = self._normalize_virtual_shell_paths(command)
        invocation = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "[Console]::InputEncoding=[Text.UTF8Encoding]::new($false); "
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "$OutputEncoding=[Text.UTF8Encoding]::new($false); "
            + command,
        ]
        try:
            result = subprocess.run(
                invocation,
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=process_timeout,
                env=self._env,
                cwd=str(self.cwd),
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: Command timed out after {timeout} seconds.",
                exit_code=124,
                truncated=False,
            )
        except Exception as exc:
            return ExecuteResponse(
                output=f"Error executing command ({type(exc).__name__}): {exc}",
                exit_code=1,
                truncated=False,
            )

        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout.rstrip())
        if result.stderr:
            parts.extend(
                f"[stderr] {line}" for line in result.stderr.rstrip().splitlines()
            )
        output = "\n".join(parts) if parts else "<no output>"
        truncated = len(output.encode("utf-8")) > self._max_output_bytes
        if truncated:
            encoded = output.encode("utf-8")[: self._max_output_bytes]
            output = encoded.decode("utf-8", errors="ignore") + (
                f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            )
        if result.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
        return ExecuteResponse(
            output=output,
            exit_code=result.returncode,
            truncated=truncated,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run integrated contract-to-matrix analysis and publish analysis JSON."
        )
    )
    parser.add_argument("--contract", type=Path, required=True, help="Path to TXT")
    parser.add_argument("--matrix", type=Path, required=True, help="Path to JSON")
    parser.add_argument("--output", type=Path, required=True, help="Result JSON path")
    parser.add_argument(
        "--analysis-output",
        type=Path,
        help="Optional path for publishing the complete analysis.v3 artifact",
    )
    return parser.parse_args(argv)


def _resolve_input(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} file does not exist: {resolved}")
    return resolved


def prepare_workspace(
    contract: Path, matrix: Path, output: Path
) -> tuple[Path, Path]:
    contract = _resolve_input(contract, "Contract")
    matrix = _resolve_input(matrix, "Matrix")
    if contract.suffix.lower() != ".txt":
        raise ValueError(f"Contract must be a .txt file: {contract}")
    if matrix.suffix.lower() != ".json":
        raise ValueError(f"Matrix must be a .json file: {matrix}")
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise ValueError(f"Output must be a .json file: {output}")
    if output in {contract, matrix}:
        raise ValueError("Output path must differ from both input paths")
    output.parent.mkdir(parents=True, exist_ok=True)

    required_project_files = (
        ANALYSIS_SKILL_SOURCE / "SKILL.md",
        FINAL_REVIEW_SKILL_SOURCE / "SKILL.md",
    )
    for source in required_project_files:
        if not source.is_file():
            raise FileNotFoundError(f"Required agent file does not exist: {source}")

    workspace = Path(tempfile.mkdtemp(prefix="contract-review-"))
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs" / "working").mkdir(parents=True)
    analysis_skill_target = workspace / "skills" / ANALYSIS_SKILL_SOURCE.name
    shutil.copytree(ANALYSIS_SKILL_SOURCE, analysis_skill_target)
    final_review_skill_target = workspace / "skills" / FINAL_REVIEW_SKILL_SOURCE.name
    shutil.copytree(FINAL_REVIEW_SKILL_SOURCE, final_review_skill_target)
    shutil.copyfile(contract, workspace / "inputs" / "contract.txt")
    shutil.copyfile(matrix, workspace / "inputs" / "matrix.json")
    return workspace, output


def resolve_analysis_output(
    path: Path | None,
    *,
    contract: Path,
    matrix: Path,
    conclusion_output: Path,
) -> Path | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != ".json":
        raise ValueError(f"Analysis output must be a .json file: {resolved}")
    forbidden = {
        contract.expanduser().resolve(),
        matrix.expanduser().resolve(),
        conclusion_output.expanduser().resolve(),
    }
    if resolved in forbidden:
        raise ValueError(
            "Analysis output path must differ from inputs and conclusion output"
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _read_json_artifact(path: Path, label: str) -> dict:
    if not path.is_file():
        raise RuntimeError(f"{label} artifact does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} artifact is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} artifact must be a JSON object: {path}")
    return payload


def validate_analysis_artifact(path: Path) -> dict:
    payload = _read_json_artifact(path, "Analysis")
    if payload.get("schema_version") != "analysis.v3":
        raise RuntimeError("Analysis schema_version must be analysis.v3")
    if payload.get("completion_status") != "complete":
        raise RuntimeError("Analysis completion_status must be complete")
    groups = payload.get("groups")
    missing = payload.get("missing_matrix_items")
    if not isinstance(groups, list) or not isinstance(missing, list):
        raise RuntimeError(
            "Analysis must contain groups and missing_matrix_items arrays"
        )

    contract_ids: set[str] = set()
    mapped_matrix_ids: set[str] = set()
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise RuntimeError(f"Analysis group {index} must be an object")
        contract_id = group.get("contract_id")
        candidates = group.get("candidates")
        status = group.get("status")
        if not isinstance(contract_id, str) or not contract_id.strip():
            raise RuntimeError(f"Analysis group {index} has invalid contract_id")
        if contract_id in contract_ids:
            raise RuntimeError(
                f"Analysis contains duplicate contract_id: {contract_id}"
            )
        contract_ids.add(contract_id)
        if not isinstance(candidates, list):
            raise RuntimeError(
                f"Analysis group {contract_id} candidates must be an array"
            )
        if status not in {"aligned", "deviation", "extra_in_contract"}:
            raise RuntimeError(
                f"Analysis group {contract_id} has invalid status: {status}"
            )

        group_matrix_ids: set[str] = set()
        for candidate_index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise RuntimeError(
                    f"Analysis candidate {contract_id}[{candidate_index}] "
                    "must be an object"
                )
            matrix_id = candidate.get("matrix_id")
            if not isinstance(matrix_id, str) or not matrix_id.strip():
                raise RuntimeError(
                    f"Analysis candidate {contract_id}[{candidate_index}] "
                    "has invalid matrix_id"
                )
            if matrix_id in group_matrix_ids:
                raise RuntimeError(
                    f"Analysis group {contract_id} repeats matrix_id: {matrix_id}"
                )
            group_matrix_ids.add(matrix_id)
            mapped_matrix_ids.add(matrix_id)

        if status == "deviation":
            differences = group.get("differences")
            if not isinstance(differences, list) or not differences:
                raise RuntimeError(
                    f"Analysis deviation group {contract_id} must contain "
                    "differences"
                )
            difference_ids: set[str] = set()
            for difference_index, difference in enumerate(differences):
                if not isinstance(difference, dict):
                    raise RuntimeError(
                        f"Analysis difference {contract_id}[{difference_index}] "
                        "must be an object"
                    )
                matrix_id = difference.get("matrix_id")
                if not isinstance(matrix_id, str) or not matrix_id.strip():
                    raise RuntimeError(
                        f"Analysis difference {contract_id}[{difference_index}] "
                        "has invalid matrix_id"
                    )
                difference_ids.add(matrix_id)
                for field in ("matrix_quote", "contract_quote", "reason"):
                    value = difference.get(field)
                    if not isinstance(value, str) or not value.strip():
                        raise RuntimeError(
                            f"Analysis difference {contract_id}"
                            f"[{difference_index}] has invalid {field}"
                        )
            unknown_ids = difference_ids.difference(group_matrix_ids)
            if unknown_ids:
                raise RuntimeError(
                    f"Analysis deviation group {contract_id} references "
                    "non-candidate matrix IDs: "
                    + ", ".join(sorted(unknown_ids))
                )
        if status == "extra_in_contract":
            if candidates:
                raise RuntimeError(
                    f"Analysis extra group {contract_id} must not contain candidates"
                )
            for field in ("contract_evidence", "reason"):
                value = group.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise RuntimeError(
                        f"Analysis extra group {contract_id} has invalid {field}"
                    )

    missing_matrix_ids: set[str] = set()
    for index, item in enumerate(missing):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"Analysis missing_matrix_items[{index}] must be an object"
            )
        matrix_id = item.get("matrix_id")
        if not isinstance(matrix_id, str) or not matrix_id.strip():
            raise RuntimeError(
                f"Analysis missing_matrix_items[{index}] has invalid matrix_id"
            )
        if matrix_id in missing_matrix_ids:
            raise RuntimeError(
                f"Analysis missing_matrix_items repeats matrix_id: {matrix_id}"
            )
        missing_matrix_ids.add(matrix_id)
        for field in ("matrix_evidence", "applicability_evidence", "reason"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(
                    f"Analysis missing matrix item {matrix_id} has invalid {field}"
                )

    overlap = mapped_matrix_ids.intersection(missing_matrix_ids)
    if overlap:
        raise RuntimeError(
            "Analysis matrix IDs cannot be both mapped and missing: "
            + ", ".join(sorted(overlap))
        )

    return payload


def _validate_nonempty_string(value, location: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Conclusion {location} must be a non-empty string")


def _validate_conclusion_items(items, location: str, *, matrix: bool) -> None:
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"Conclusion {location} must be a non-empty array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise RuntimeError(f"Conclusion {location}[{index}] must be an object")
        allowed = {"id", "text", "main_idea"} if matrix else {"id", "text"}
        unknown = set(item).difference(allowed)
        if unknown:
            raise RuntimeError(
                f"Conclusion {location}[{index}] has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        _validate_nonempty_string(item.get("id"), f"{location}[{index}].id")
        _validate_nonempty_string(item.get("text"), f"{location}[{index}].text")
        if "main_idea" in item:
            _validate_nonempty_string(
                item["main_idea"], f"{location}[{index}].main_idea"
            )


def validate_conclusion_artifact(path: Path) -> dict:
    payload = _read_json_artifact(path, "Conclusion")
    if payload.get("schema_version") != "conclusion.v1":
        raise RuntimeError("Conclusion schema_version must be conclusion.v1")
    if payload.get("completion_status") != "complete":
        raise RuntimeError("Conclusion completion_status must be complete")
    if set(payload).difference({"schema_version", "completion_status", "findings"}):
        raise RuntimeError("Conclusion contains unsupported top-level fields")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise RuntimeError("Conclusion findings must be an array")

    for index, finding in enumerate(findings):
        location = f"findings[{index}]"
        if not isinstance(finding, dict):
            raise RuntimeError(f"Conclusion {location} must be an object")
        status = finding.get("status")
        if status not in {
            "deviation",
            "missing_in_contract",
            "extra_in_contract",
        }:
            raise RuntimeError(f"Conclusion {location} has invalid status: {status}")
        allowed = {
            "status",
            "contract_items",
            "matrix_items",
            "comment",
            "evidence",
        }
        unknown = set(finding).difference(allowed)
        if unknown:
            raise RuntimeError(
                f"Conclusion {location} has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        _validate_nonempty_string(finding.get("comment"), f"{location}.comment")

        if status in {"deviation", "extra_in_contract"}:
            _validate_conclusion_items(
                finding.get("contract_items"),
                f"{location}.contract_items",
                matrix=False,
            )
        elif "contract_items" in finding:
            raise RuntimeError(
                f"Conclusion {location} missing finding must omit contract_items"
            )

        if status in {"deviation", "missing_in_contract"}:
            _validate_conclusion_items(
                finding.get("matrix_items"),
                f"{location}.matrix_items",
                matrix=True,
            )
        elif "matrix_items" in finding:
            raise RuntimeError(
                f"Conclusion {location} extra finding must omit matrix_items"
            )

        evidence = finding.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise RuntimeError(
                f"Conclusion {location}.evidence must be a non-empty array"
            )
        required_evidence = {
            "deviation": {"matrix_id", "matrix_quote", "contract_quote"},
            "missing_in_contract": {"matrix_id", "matrix_quote"},
            "extra_in_contract": {"contract_id", "contract_quote"},
        }[status]
        for evidence_index, item in enumerate(evidence):
            evidence_location = f"{location}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"Conclusion {evidence_location} must be an object"
                )
            if set(item) != required_evidence:
                raise RuntimeError(
                    f"Conclusion {evidence_location} must contain exactly: "
                    + ", ".join(sorted(required_evidence))
                )
            for field in required_evidence:
                _validate_nonempty_string(
                    item.get(field), f"{evidence_location}.{field}"
                )
    return payload


def build_backend(workspace: Path) -> WindowsPowerShellBackend:
    shell_env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "PATHEXT",
            "SystemRoot",
            "WINDIR",
            "ComSpec",
            "TEMP",
            "TMP",
            "LOCALAPPDATA",
            "APPDATA",
        )
        if key in os.environ
    }
    shell_env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "DEEPAGENT_WORKSPACE_ROOT": str(workspace.resolve()),
        }
    )
    return WindowsPowerShellBackend(
        root_dir=workspace,
        virtual_mode=True,
        max_output_bytes=400_000,
        env=shell_env,
        inherit_env=False,
    )


def build_agent(backend: WindowsPowerShellBackend):
    model = get_llm()
    return create_deep_agent(
        name="integrated-contract-analysis",
        model=model,
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
        backend=backend,
        skills=[],
        subagents=[
            {
                "name": "analyzer",
                "description": (
                    "Находит все юридические аналоги, deviations, extra и "
                    "обязательные применимые missing во всём договоре и записывает "
                    "полный analysis.v3."
                ),
                "system_prompt": ANALYZER_SYSTEM_PROMPT,
                "skills": ["/skills/integrated-contract-analysis/"],
            },
            {
                "name": "final-reviewer",
                "description": (
                    "Отбирает из полного analysis.v3 приоритетные deviations, "
                    "значимые extra и применимые обязательные missing и записывает "
                    "короткий conclusion.v1."
                ),
                "system_prompt": FINAL_REVIEWER_SYSTEM_PROMPT,
                "skills": ["/skills/final-finding-review/"],
            },
        ],
    )


def run_agent(workspace: Path) -> None:
    agent = build_agent(build_backend(workspace))
    retry_number = 0
    thread_id = uuid.uuid4().hex
    retryable_errors = (
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )
    while True:
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": RUN_PROMPT}]},
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 10_000,
                    "callbacks": [CompactTraceHandler()],
                },
            )
            return
        except retryable_errors as exc:
            retry_number += 1
            delay_seconds = min(2**retry_number, 30)
            print(
                f"Transient failure ({type(exc).__name__}); retrying the same "
                f"workspace in {delay_seconds}s [retry {retry_number}]",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay_seconds)


def _artifact_is_valid(path: Path, validator) -> bool:
    try:
        validator(path)
    except RuntimeError:
        return False
    return True


def run_final_reviewer(workspace: Path, *, artifact_attempts: int = 3) -> None:
    register_harness_profile(
        "openai",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )
    agent = create_deep_agent(
        name="final-finding-selector",
        model=get_llm(),
        system_prompt=FINAL_REVIEWER_SYSTEM_PROMPT,
        backend=build_backend(workspace),
        skills=["/skills/final-finding-review/"],
        subagents=[],
    )
    generated = workspace / "outputs" / "working" / "final-result.json"
    retryable_errors = (
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )

    for artifact_attempt in range(1, artifact_attempts + 1):
        retry_number = 0
        while True:
            try:
                agent.invoke(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": FINAL_REVIEW_RECOVERY_PROMPT,
                            }
                        ]
                    },
                    config={
                        "configurable": {"thread_id": uuid.uuid4().hex},
                        "recursion_limit": 10_000,
                        "callbacks": [CompactTraceHandler()],
                    },
                )
                break
            except retryable_errors as exc:
                retry_number += 1
                delay_seconds = min(2**retry_number, 30)
                print(
                    f"Reviewer transport failure ({type(exc).__name__}); "
                    f"retrying in {delay_seconds}s [retry {retry_number}]",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay_seconds)

        try:
            validate_conclusion_artifact(generated)
            return
        except RuntimeError as exc:
            if artifact_attempt == artifact_attempts:
                raise
            print(
                f"Reviewer returned an invalid artifact: {exc}; asking it to "
                f"repair the same path [attempt {artifact_attempt + 1}/"
                f"{artifact_attempts}]",
                file=sys.stderr,
                flush=True,
            )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    workspace: Path | None = None
    completed = False
    try:
        workspace, output = prepare_workspace(args.contract, args.matrix, args.output)
        analysis_output = resolve_analysis_output(
            args.analysis_output,
            contract=args.contract,
            matrix=args.matrix,
            conclusion_output=output,
        )
        analysis = workspace / "outputs" / "working" / "analysis.json"
        conclusion = workspace / "outputs" / "working" / "final-result.json"
        orchestration_error: Exception | None = None
        try:
            run_agent(workspace)
        except Exception as exc:
            orchestration_error = exc

        if not _artifact_is_valid(analysis, validate_analysis_artifact):
            if orchestration_error is not None:
                raise RuntimeError(
                    "Orchestrator failed before producing a valid analysis "
                    f"checkpoint: {type(orchestration_error).__name__}: "
                    f"{orchestration_error}"
                ) from orchestration_error

        validate_analysis_artifact(analysis)
        if analysis_output is not None:
            shutil.copyfile(analysis, analysis_output)

        if not _artifact_is_valid(conclusion, validate_conclusion_artifact):
            print(
                "Conclusion checkpoint is missing or invalid; running only "
                "final-reviewer against the validated analysis checkpoint.",
                file=sys.stderr,
                flush=True,
            )
            run_final_reviewer(workspace)

        validate_conclusion_artifact(conclusion)
        shutil.copyfile(conclusion, output)
        completed = True
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            if completed:
                shutil.rmtree(workspace, ignore_errors=True)
            else:
                print(
                    f"[workspace] preserved after failure: {workspace}",
                    file=sys.stderr,
                    flush=True,
                )

    print(f"[total] finished in {time.perf_counter() - started:.1f}s", flush=True)
    if analysis_output is not None:
        print(f"[analysis] {analysis_output}", flush=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
