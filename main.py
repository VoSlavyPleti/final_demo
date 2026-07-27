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
from langchain_core.callbacks import BaseCallbackHandler

from llm import get_llm


PROJECT_ROOT = Path(__file__).resolve().parent
PRIMARY_SKILL_SOURCE = PROJECT_ROOT / "skills" / "primary-contract-analysis"
GAP_SKILL_SOURCE = PROJECT_ROOT / "skills" / "matrix-gap-recovery"
SELECTION_SKILL_SOURCE = PROJECT_ROOT / "skills" / "final-finding-selection"
SKILL_SOURCES = (
    PRIMARY_SKILL_SOURCE,
    GAP_SKILL_SOURCE,
    SELECTION_SKILL_SOURCE,
)

PRIMARY_ARTIFACT = Path("outputs/working/primary-analysis.json")
COMPLETE_ARTIFACT = Path("outputs/working/complete-analysis.json")
FINAL_ARTIFACT = Path("outputs/working/final-result.json")


ORCHESTRATOR_SYSTEM_PROMPT = """
Ты маршрутизируешь три последовательных файловых этапа. Не выполняй
юридический анализ и не читай исходные документы или содержимое артефактов.

1. Вызови `primary-analyzer` с входами `/inputs/contract.txt`,
   `/inputs/matrix.json` и выходом
   `/outputs/working/primary-analysis.json`.
2. Только после успешного завершения вызови `matrix-gap-recovery` с этим
   артефактом, теми же входами и выходом
   `/outputs/working/complete-analysis.json`.
3. Только после успешного завершения вызови `final-selector` с
   `/outputs/working/complete-analysis.json` и выходом
   `/outputs/working/final-result.json`.

Все роли используют общий файловый backend. Не копируй содержимое артефактов в
сообщения и не заменяй назначенные роли general-purpose агентом. При ошибке
этапа прекрати сценарий. При успехе верни только путь итогового файла.
""".strip()

RUN_PROMPT = """
Выполни `primary-analyzer`, затем `matrix-gap-recovery`, затем
`final-selector`. Используй только назначенные пути:

- `/inputs/contract.txt`;
- `/inputs/matrix.json`;
- `/outputs/working/primary-analysis.json`;
- `/outputs/working/complete-analysis.json`;
- `/outputs/working/final-result.json`.
""".strip()

PRIMARY_ONLY_ORCHESTRATOR_SYSTEM_PROMPT = """
Ты маршрутизируешь один этап. Не выполняй юридический анализ и не читай
исходные документы. Вызови только `primary-analyzer` для
`/inputs/contract.txt` и `/inputs/matrix.json` с выходом
`/outputs/working/primary-analysis.json`. После завершения верни только путь
артефакта. Не вызывай другие роли или general-purpose агента.
""".strip()

PRIMARY_ONLY_RUN_PROMPT = """
Вызови только `primary-analyzer` и получи
`/outputs/working/primary-analysis.json` для `/inputs/contract.txt` и
`/inputs/matrix.json`.
""".strip()

SELECTION_ONLY_ORCHESTRATOR_SYSTEM_PROMPT = """
Ты маршрутизируешь один этап. Не выполняй юридический анализ и не читай
содержимое артефакта. Вызови только `final-selector` для
`/outputs/working/complete-analysis.json` с выходом
`/outputs/working/final-result.json`. После завершения верни только путь
итогового файла. Не вызывай другие роли или general-purpose агента.
""".strip()

SELECTION_ONLY_RUN_PROMPT = """
Вызови только `final-selector` для
`/outputs/working/complete-analysis.json` и получи
`/outputs/working/final-result.json`.
""".strip()

PRIMARY_SYSTEM_PROMPT = """
Выполни `/skills/primary-contract-analysis/SKILL.md` для назначенных входов.
Запиши валидный `primary-analysis.v1` строго в
`/outputs/working/primary-analysis.json`. Перед завершением собери сохранённые
порции, сверь итог со своим полным реестром пунктов и прочитай файл обратно. Не
помечай префикс договора как complete. Не возвращай анализ в сообщении; верни
путь и краткое подтверждение завершения.
""".strip()

GAP_SYSTEM_PROMPT = """
Выполни `/skills/matrix-gap-recovery/SKILL.md` для назначенного primary
артефакта и исходных входов. Запиши валидный `complete-analysis.v1` строго в
`/outputs/working/complete-analysis.json`. Перед завершением прочитай файл
обратно. Не возвращай анализ в сообщении; верни путь и краткое подтверждение
завершения.
""".strip()

SELECTION_SYSTEM_PROMPT = """
Выполни `/skills/final-finding-selection/SKILL.md` для назначенного complete
analysis. Запиши валидный `conclusion.v2` строго в
`/outputs/working/final-result.json`. Перед завершением прочитай файл обратно.
Не возвращай findings в сообщении; верни путь и краткое подтверждение
завершения.
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
        description="Run contract review and publish a conclusion.v2 JSON artifact."
    )
    parser.add_argument("--contract", type=Path, required=True, help="Path to TXT")
    parser.add_argument("--matrix", type=Path, required=True, help="Path to JSON")
    parser.add_argument("--output", type=Path, required=True, help="Conclusion JSON")
    parser.add_argument(
        "--analysis-output",
        type=Path,
        help="Optional path for publishing complete-analysis.v1",
    )
    parser.add_argument(
        "--primary-output",
        type=Path,
        help="Optional path for publishing primary-analysis.v1",
    )
    return parser.parse_args(argv)


def _resolve_input(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} file does not exist: {resolved}")
    return resolved


def _resolve_json_output(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() != ".json":
        raise ValueError(f"{label} must be a .json file: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
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
    output = _resolve_json_output(output, "Output")
    if output in {contract, matrix}:
        raise ValueError("Output path must differ from both input paths")

    for skill_source in SKILL_SOURCES:
        required = skill_source / "SKILL.md"
        if not required.is_file():
            raise FileNotFoundError(f"Required agent file does not exist: {required}")

    workspace = Path(tempfile.mkdtemp(prefix="contract-review-"))
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs" / "working").mkdir(parents=True)
    for skill_source in SKILL_SOURCES:
        shutil.copytree(
            skill_source,
            workspace / "skills" / skill_source.name,
        )
    shutil.copyfile(contract, workspace / "inputs" / "contract.txt")
    shutil.copyfile(matrix, workspace / "inputs" / "matrix.json")
    return workspace, output


def resolve_publish_outputs(
    *,
    primary_path: Path | None,
    analysis_path: Path | None,
    contract: Path,
    matrix: Path,
    conclusion_output: Path,
) -> tuple[Path | None, Path | None]:
    forbidden = {
        contract.expanduser().resolve(),
        matrix.expanduser().resolve(),
        conclusion_output.expanduser().resolve(),
    }

    def resolve(path: Path | None, label: str) -> Path | None:
        if path is None:
            return None
        resolved = _resolve_json_output(path, label)
        if resolved in forbidden:
            raise ValueError(f"{label} must differ from inputs and other outputs")
        forbidden.add(resolved)
        return resolved

    primary_output = resolve(primary_path, "Primary output")
    analysis_output = resolve(analysis_path, "Analysis output")
    return primary_output, analysis_output


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


def _validate_common(
    path: Path,
    *,
    label: str,
    schema_version: str,
    list_fields: tuple[str, ...],
) -> dict:
    payload = _read_json_artifact(path, label)
    if payload.get("schema_version") != schema_version:
        raise RuntimeError(f"{label} schema_version must be {schema_version}")
    if payload.get("completion_status") != "complete":
        raise RuntimeError(f"{label} completion_status must be complete")
    for field in list_fields:
        if not isinstance(payload.get(field), list):
            raise RuntimeError(f"{label} field {field!r} must be an array")
    return payload


def validate_primary_artifact(path: Path) -> dict:
    return _validate_common(
        path,
        label="Primary analysis",
        schema_version="primary-analysis.v1",
        list_fields=("groups",),
    )


def validate_complete_artifact(path: Path) -> dict:
    return _validate_common(
        path,
        label="Complete analysis",
        schema_version="complete-analysis.v1",
        list_fields=("groups", "matrix_audit"),
    )


def validate_conclusion_artifact(path: Path) -> dict:
    return _validate_common(
        path,
        label="Conclusion",
        schema_version="conclusion.v2",
        list_fields=("findings",),
    )


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


def _subagent_definitions() -> list[dict]:
    return [
        {
            "name": "primary-analyzer",
            "description": (
                "Строит contract-oriented many-to-many mapping и сразу "
                "назначает aligned, deviation или provisional extra; пишет "
                "primary-analysis.v1."
            ),
            "system_prompt": PRIMARY_SYSTEM_PROMPT,
            "skills": ["/skills/primary-contract-analysis/"],
        },
        {
            "name": "matrix-gap-recovery",
            "description": (
                "Проверяет только непокрытые matrix ID, absent-аспекты и "
                "provisional extra; восстанавливает связи и пишет "
                "complete-analysis.v1."
            ),
            "system_prompt": GAP_SYSTEM_PROMPT,
            "skills": ["/skills/matrix-gap-recovery/"],
        },
        {
            "name": "final-selector",
            "description": (
                "Не меняя mapping и статусы, отбирает значимые deviation, "
                "mandatory missing и самостоятельные extra; пишет conclusion.v2."
            ),
            "system_prompt": SELECTION_SYSTEM_PROMPT,
            "skills": ["/skills/final-finding-selection/"],
        },
    ]


def build_agent(
    backend: WindowsPowerShellBackend,
    *,
    system_prompt: str = ORCHESTRATOR_SYSTEM_PROMPT,
):
    return create_deep_agent(
        name="contract-review-orchestrator",
        model=get_llm(),
        system_prompt=system_prompt,
        backend=backend,
        skills=[],
        subagents=_subagent_definitions(),
    )


def run_agent(
    workspace: Path,
    *,
    run_prompt: str = RUN_PROMPT,
    system_prompt: str = ORCHESTRATOR_SYSTEM_PROMPT,
) -> None:
    agent = build_agent(
        build_backend(workspace),
        system_prompt=system_prompt,
    )
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
                {"messages": [{"role": "user", "content": run_prompt}]},
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    started_wall = time.time()
    workspace: Path | None = None
    completed = False
    stage_timings: dict[str, float] | None = None
    try:
        workspace, conclusion_output = prepare_workspace(
            args.contract,
            args.matrix,
            args.output,
        )
        primary_output, analysis_output = resolve_publish_outputs(
            primary_path=args.primary_output,
            analysis_path=args.analysis_output,
            contract=args.contract,
            matrix=args.matrix,
            conclusion_output=conclusion_output,
        )

        run_agent(workspace)

        primary = workspace / PRIMARY_ARTIFACT
        complete = workspace / COMPLETE_ARTIFACT
        conclusion = workspace / FINAL_ARTIFACT
        validate_primary_artifact(primary)
        validate_complete_artifact(complete)
        validate_conclusion_artifact(conclusion)
        primary_finished = primary.stat().st_mtime
        recovery_finished = complete.stat().st_mtime
        selector_finished = conclusion.stat().st_mtime
        stage_timings = {
            "primary": max(0.0, primary_finished - started_wall),
            "gap_recovery": max(0.0, recovery_finished - primary_finished),
            "selector": max(0.0, selector_finished - recovery_finished),
        }

        if primary_output is not None:
            shutil.copyfile(primary, primary_output)
        if analysis_output is not None:
            shutil.copyfile(complete, analysis_output)
        shutil.copyfile(conclusion, conclusion_output)
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
    if stage_timings is not None:
        print(
            "[stages] "
            + " ".join(
                f"{name}={duration:.1f}s"
                for name, duration in stage_timings.items()
            ),
            flush=True,
        )
    if primary_output is not None:
        print(f"[primary] {primary_output}", flush=True)
    if analysis_output is not None:
        print(f"[analysis] {analysis_output}", flush=True)
    print(conclusion_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
