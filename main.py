from __future__ import annotations

import argparse
import hashlib
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
from langgraph.checkpoint.memory import MemorySaver

from llm import get_llm


PROJECT_ROOT = Path(__file__).resolve().parent
DOMAIN_SKILL_SOURCE = PROJECT_ROOT / "skills" / "contract-matrix-review"
SKILL_SOURCES = (DOMAIN_SKILL_SOURCE,)

RESULT_ARTIFACT = Path("outputs/result.json")
RUN_MANIFEST = Path("outputs/run-manifest.json")


AGENT_SYSTEM_PROMPT = """
Ты — агент сравнения проектов договоров с банковской матрицей.

Исходники находятся в `/inputs/contract.txt` и `/inputs/matrix.json`.
Результат должен быть записан в `/outputs/result.json`.
Для анализа обязательно используй skill `/skills/contract-matrix-review/`.
При делегировании анализа укажи subagent использовать этот же skill.

В сообщении верни только краткое подтверждение и путь.
""".strip()

RUN_PROMPT = """
Сравни проект договора с банковской матрицей.
Сохрани результат в `/outputs/result.json`.
""".strip()

class CompactTraceHandler(BaseCallbackHandler):
    """Emit timing events without prompts, source text, or tool payloads."""

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
        description="Run the autonomous contract-matrix Deep Agent harness."
    )
    parser.add_argument("--contract", type=Path, required=True, help="Path to TXT")
    parser.add_argument("--matrix", type=Path, required=True, help="Path to JSON")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Full contract-matrix mapping JSON",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Preserve the temporary workspace after a successful run",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum transient API retries; default: 3",
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
) -> tuple[Path, Path, Path, Path]:
    contract = _resolve_input(contract, "Contract")
    matrix = _resolve_input(matrix, "Matrix")
    if contract.suffix.lower() != ".txt":
        raise ValueError(f"Contract must be a .txt file: {contract}")
    if matrix.suffix.lower() != ".json":
        raise ValueError(f"Matrix must be a .json file: {matrix}")
    output = _resolve_json_output(output, "Output")
    if output in {contract, matrix}:
        raise ValueError("Output path must differ from both input paths")

    required_skill = DOMAIN_SKILL_SOURCE / "SKILL.md"
    if not required_skill.is_file():
        raise FileNotFoundError(f"Required agent file does not exist: {required_skill}")

    workspace = Path(tempfile.mkdtemp(prefix="contract-review-"))
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs" / "working").mkdir(parents=True)
    shutil.copytree(
        DOMAIN_SKILL_SOURCE,
        workspace / "skills" / DOMAIN_SKILL_SOURCE.name,
    )
    mounted_contract = workspace / "inputs" / "contract.txt"
    mounted_matrix = workspace / "inputs" / "matrix.json"
    shutil.copyfile(contract, mounted_contract)
    shutil.copyfile(matrix, mounted_matrix)
    return workspace, output, contract, matrix


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


def _require_list(payload: dict, field: str, label: str) -> list:
    value = payload.get(field)
    if not isinstance(value, list):
        raise RuntimeError(f"{label} field {field!r} must be an array")
    return value


def validate_result_artifact(path: Path) -> dict:
    """Validate only the transport/schema contract, never legal conclusions."""

    payload = _read_json_artifact(path, "Result")
    if payload.get("schema_version") != "contract-matrix-map.v3":
        raise RuntimeError(
            "Result schema_version must be contract-matrix-map.v3"
        )
    if payload.get("completion_status") != "complete":
        raise RuntimeError("Result completion_status must be complete")

    contract_items = _require_list(payload, "contract_items", "Result")
    matrix_items = _require_list(payload, "matrix_items", "Result")

    contract_ids: set[str] = set()
    referenced_matrix_ids: set[str] = set()
    for index, item in enumerate(contract_items):
        label = f"Result contract_items[{index}]"
        if not isinstance(item, dict):
            raise RuntimeError(f"{label} must be an object")
        if not isinstance(item.get("contract_id"), str) or not item["contract_id"]:
            raise RuntimeError(f"{label} contract_id is required")
        if item["contract_id"] in contract_ids:
            raise RuntimeError(f"{label} duplicates contract_id")
        contract_ids.add(item["contract_id"])
        if not isinstance(item.get("contract_text"), str):
            raise RuntimeError(f"{label} contract_text is required")
        if item.get("status") not in {
            "aligned",
            "deviation",
            "extra_in_contract",
            "not_applicable",
        }:
            raise RuntimeError(f"{label} has invalid status")
        matrix_ids = _require_list(item, "matrix_ids", label)
        if len(matrix_ids) != len(set(matrix_ids)):
            raise RuntimeError(f"{label} contains duplicate matrix_ids")
        for matrix_index, matrix_id in enumerate(matrix_ids):
            if not isinstance(matrix_id, str) or not matrix_id:
                raise RuntimeError(
                    f"{label}.matrix_ids[{matrix_index}] is invalid"
                )
            referenced_matrix_ids.add(matrix_id)
        if (
            not isinstance(item.get("comment"), str)
            or not item["comment"].strip()
        ):
            raise RuntimeError(f"{label} comment is required")

    matrix_ids: set[str] = set()
    for index, item in enumerate(matrix_items):
        label = f"Result matrix_items[{index}]"
        if not isinstance(item, dict):
            raise RuntimeError(f"{label} must be an object")
        if not isinstance(item.get("matrix_id"), str) or not item["matrix_id"]:
            raise RuntimeError(f"{label} matrix_id is required")
        if item["matrix_id"] in matrix_ids:
            raise RuntimeError(f"{label} duplicates matrix_id")
        matrix_ids.add(item["matrix_id"])
        if not isinstance(item.get("matrix_text"), str):
            raise RuntimeError(f"{label} matrix_text is required")
        if item.get("required_type") != "mandatory":
            raise RuntimeError(f"{label} required_type must be mandatory")
        if item.get("status") != "missing_in_contract":
            raise RuntimeError(
                f"{label} status must be missing_in_contract"
            )
        if (
            not isinstance(item.get("comment"), str)
            or not item["comment"].strip()
        ):
            raise RuntimeError(f"{label} comment is required")

    conflicting_matrix_ids = referenced_matrix_ids & matrix_ids
    if conflicting_matrix_ids:
        raise RuntimeError(
            "Result matrix_items contain matrix_ids already referenced by "
            "contract_items: "
            + ", ".join(sorted(conflicting_matrix_ids))
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


def build_agent(
    backend: WindowsPowerShellBackend,
    *,
    checkpointer=None,
):
    return create_deep_agent(
        name="contract-matrix-review-agent",
        model=get_llm(),
        system_prompt=AGENT_SYSTEM_PROMPT,
        backend=backend,
        skills=["/skills/contract-matrix-review/"],
        checkpointer=checkpointer or MemorySaver(),
    )


def run_agent(
    workspace: Path,
    *,
    max_retries: int = 3,
    thread_id: str | None = None,
    sleep=time.sleep,
) -> None:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    agent = build_agent(build_backend(workspace), checkpointer=MemorySaver())
    stable_thread_id = thread_id or uuid.uuid4().hex
    retryable_errors = (
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )

    for attempt in range(max_retries + 1):
        prompt = (
            RUN_PROMPT
            if attempt == 0
            else (
                "Продолжи незавершённый анализ в том же thread и workspace. "
                "Проверь существующие рабочие файлы и доведи канонический "
                "`/outputs/result.json` до complete."
            )
        )
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "configurable": {"thread_id": stable_thread_id},
                    "recursion_limit": 10_000,
                    "callbacks": [CompactTraceHandler()],
                },
            )
            return
        except retryable_errors as exc:
            if attempt >= max_retries:
                raise RuntimeError(
                    f"Agent failed after {max_retries} transient retries"
                ) from exc
            delay_seconds = min(2 ** (attempt + 1), 30)
            print(
                f"Transient failure ({type(exc).__name__}); continuing the "
                f"same thread in {delay_seconds}s "
                f"[retry {attempt + 1}/{max_retries}]",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay_seconds)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(
    workspace: Path,
    *,
    run_id: str,
    thread_id: str,
    status: str,
    contract: Path,
    matrix: Path,
    error: str | None = None,
) -> None:
    payload = {
        "schema_version": "contract-review-run.v1",
        "run_id": run_id,
        "thread_id": thread_id,
        "status": status,
        "contract_sha256": _sha256(contract),
        "matrix_sha256": _sha256(matrix),
        "skill": "contract-matrix-review",
        "error": error,
    }
    target = workspace / RUN_MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.perf_counter()
    workspace: Path | None = None
    completed = False
    run_id = uuid.uuid4().hex
    thread_id = uuid.uuid4().hex

    try:
        if args.max_retries < 0:
            raise ValueError("--max-retries must be non-negative")
        workspace, result_output, contract, matrix = prepare_workspace(
            args.contract,
            args.matrix,
            args.output,
        )
        _write_manifest(
            workspace,
            run_id=run_id,
            thread_id=thread_id,
            status="in_progress",
            contract=contract,
            matrix=matrix,
        )

        run_agent(
            workspace,
            max_retries=args.max_retries,
            thread_id=thread_id,
        )

        result = workspace / RESULT_ARTIFACT
        validate_result_artifact(result)
        shutil.copy2(result, result_output)
        _write_manifest(
            workspace,
            run_id=run_id,
            thread_id=thread_id,
            status="complete",
            contract=contract,
            matrix=matrix,
        )
        completed = True
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        if workspace is not None:
            try:
                contract_path = _resolve_input(args.contract, "Contract")
                matrix_path = _resolve_input(args.matrix, "Matrix")
                _write_manifest(
                    workspace,
                    run_id=run_id,
                    thread_id=thread_id,
                    status="failed",
                    contract=contract_path,
                    matrix=matrix_path,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            if completed and not args.keep_workspace:
                shutil.rmtree(workspace, ignore_errors=True)
            else:
                print(
                    f"[workspace] preserved: {workspace}",
                    file=sys.stderr,
                    flush=True,
                )

    print(f"[agent] finished in {time.perf_counter() - started:.1f}s", flush=True)
    print(result_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
