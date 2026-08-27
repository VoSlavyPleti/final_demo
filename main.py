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
PROMPTS_ROOT = PROJECT_ROOT / "prompts"
PYTHON_RUNTIME_ROOT = PROJECT_ROOT / "harness_runtime"

RESULT_ARTIFACT = Path("outputs/result.json")
RUN_MANIFEST = Path("outputs/run-manifest.json")
TRACE_ARTIFACT = Path("outputs/run-trace.jsonl")


def _load_prompt(name: str) -> str:
    return (PROMPTS_ROOT / name).read_text(encoding="utf-8").strip()


AGENT_SYSTEM_PROMPT = _load_prompt("orchestrator-system.md")
RUN_PROMPT = _load_prompt("contract-review-user.md")

class CompactTraceHandler(BaseCallbackHandler):
    """Persist timing and payload fingerprints without storing source text."""

    run_inline = True
    _VIRTUAL_PATH = re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z]:)?[/\\]?"
        r"(?:inputs|outputs|skills|tmp)(?:[/\\][\w.()-]+)+"
    )

    def __init__(self, trace_path: Path | None = None) -> None:
        self._started: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._trace_path = trace_path
        if trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.touch()

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
    def _fingerprint(value) -> dict[str, int | str]:
        if isinstance(value, bytes):
            encoded = value
        elif isinstance(value, str):
            encoded = value.encode("utf-8", errors="replace")
        else:
            encoded = repr(value).encode("utf-8", errors="replace")
        return {
            "size_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    @classmethod
    def _safe_paths(cls, value: str) -> list[str]:
        paths = {
            match.group(0).replace("\\", "/").lstrip("/")
            for match in cls._VIRTUAL_PATH.finditer(value)
        }
        return sorted(paths)

    def _emit(self, event: str, run_id, parent_run_id, **fields) -> None:
        payload = {
            "event": event,
            "timestamp": round(time.time(), 3),
            "run_id": str(run_id),
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
            **fields,
        }
        serialized = json.dumps(payload, ensure_ascii=True)
        with self._lock:
            print("[trace] " + serialized, flush=True)
            if self._trace_path is not None:
                with self._trace_path.open("a", encoding="utf-8") as stream:
                    stream.write(serialized + "\n")

    def _begin(
        self, kind: str, name: str, run_id, parent_run_id, **fields
    ) -> None:
        key = str(run_id)
        with self._lock:
            if key in self._started:
                return
            self._started[key] = (kind, time.perf_counter())
        self._emit(f"{kind}_start", run_id, parent_run_id, name=name, **fields)

    def _finish(
        self,
        kind: str,
        run_id,
        parent_run_id,
        error: str | None = None,
        **fields,
    ) -> None:
        key = str(run_id)
        with self._lock:
            started = self._started.pop(key, None)
        duration = time.perf_counter() - started[1] if started else None
        event_fields = {
            "duration_seconds": round(duration, 3) if duration is not None else None
        }
        event_fields.update(fields)
        if error is not None:
            event_fields["error_type"] = error
        suffix = "error" if error else "end"
        self._emit(f"{kind}_{suffix}", run_id, parent_run_id, **event_fields)

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        message_count = sum(len(batch) for batch in messages)
        self._begin(
            "model",
            self._name(serialized, "chat_model"),
            run_id,
            parent_run_id,
            message_count=message_count,
        )

    def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        self._begin(
            "model",
            self._name(serialized, "llm"),
            run_id,
            parent_run_id,
            prompt_count=len(prompts),
        )

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("model", run_id, parent_run_id)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("model", run_id, parent_run_id, type(error).__name__)

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        fingerprint = self._fingerprint(input_str)
        self._begin(
            "tool",
            self._name(serialized, "tool"),
            run_id,
            parent_run_id,
            input_size_bytes=fingerprint["size_bytes"],
            input_sha256=fingerprint["sha256"],
            paths=self._safe_paths(input_str),
        )

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs) -> None:
        fingerprint = self._fingerprint(output)
        self._finish(
            "tool",
            run_id,
            parent_run_id,
            output_size_bytes=fingerprint["size_bytes"],
            output_sha256=fingerprint["sha256"],
        )

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._finish("tool", run_id, parent_run_id, type(error).__name__)


class WindowsPowerShellBackend(LocalShellBackend):
    """LocalShellBackend whose execute tool uses PowerShell on Windows."""

    _VIRTUAL_SHELL_PATH = re.compile(
        r"(?<![A-Za-z0-9_])(?:[A-Za-z]:)?[/\\](inputs|outputs|skills)"
        r"(?=(?:[/\\]|[\s'\"`;,)\]}])|$)",
        re.IGNORECASE,
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
        help="Maximum retries for transient API failures; default: 3",
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


def build_backend(workspace: Path) -> WindowsPowerShellBackend:
    python_runtime = PYTHON_RUNTIME_ROOT.resolve()
    if not (python_runtime / "sitecustomize.py").is_file():
        raise FileNotFoundError(
            f"Python workspace runtime is missing: {python_runtime / 'sitecustomize.py'}"
        )

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
            "PYTHONPATH": str(python_runtime),
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
    model = get_llm()
    return create_deep_agent(
        name="contract-matrix-review-agent",
        model=model,
        system_prompt=AGENT_SYSTEM_PROMPT,
        backend=backend,
        skills=["/skills/contract-matrix-review/"],
        checkpointer=checkpointer or MemorySaver(),
    )


def _read_json_object(path: Path, label: str) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"{label} is missing: /{path.as_posix()}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"{label} is not readable UTF-8 JSON: {type(exc).__name__}"]
    if not isinstance(payload, dict):
        return None, [f"{label} must be a JSON object"]
    return payload, []


def quality_gate_failures(workspace: Path) -> list[str]:
    """Validate the published JSON shape; legal judgments remain agent-owned."""

    failures: list[str] = []
    result, errors = _read_json_object(workspace / RESULT_ARTIFACT, "result")
    failures.extend(errors)
    if result is None:
        return failures

    required_top_keys = {"schema_version", "contract_items", "matrix_items"}
    missing_top_keys = sorted(required_top_keys - result.keys())
    unexpected_top_keys = sorted(result.keys() - required_top_keys)
    if missing_top_keys:
        failures.append(
            "result is missing top-level keys: " + ", ".join(missing_top_keys)
        )
    if unexpected_top_keys:
        failures.append(
            "result has unsupported top-level keys: " + ", ".join(unexpected_top_keys)
        )
    if result.get("schema_version") != "contract-matrix-map.v6":
        failures.append("result schema_version must be contract-matrix-map.v6")

    contract_items = result.get("contract_items")
    if not isinstance(contract_items, list):
        failures.append("result contract_items must be a list")
        contract_items = []

    matrix_items = result.get("matrix_items")
    if not isinstance(matrix_items, list):
        failures.append("result matrix_items must be a list")
        matrix_items = []

    contract_statuses = {
        "aligned",
        "deviation",
        "extra_in_contract",
        "not_applicable",
        "needs_review",
    }
    contract_occurrences: dict[str, list[tuple[int, str | None]]] = {}
    mapped_matrix_ids: set[str] = set()
    required_contract_keys = {"contract_id", "matrix_ids", "status", "comment"}
    allowed_contract_keys = required_contract_keys | {"source_locator"}

    for index, item in enumerate(contract_items):
        label = f"contract_items[{index}]"
        if not isinstance(item, dict):
            failures.append(f"{label} must be an object")
            continue

        missing_keys = sorted(required_contract_keys - item.keys())
        unexpected_keys = sorted(item.keys() - allowed_contract_keys)
        if missing_keys:
            failures.append(f"{label} is missing keys: " + ", ".join(missing_keys))
        if unexpected_keys:
            failures.append(
                f"{label} has unsupported keys: " + ", ".join(unexpected_keys)
            )

        contract_id = item.get("contract_id")
        if not isinstance(contract_id, str) or not contract_id.strip():
            failures.append(f"{label} contract_id must be a non-empty string")
        else:
            source_locator = item.get("source_locator")
            if source_locator is not None and (
                not isinstance(source_locator, str) or not source_locator.strip()
            ):
                failures.append(
                    f"{label} source_locator must be a non-empty string when present"
                )
                source_locator = None
            contract_occurrences.setdefault(contract_id, []).append(
                (index, source_locator)
            )

        status = item.get("status")
        if not isinstance(status, str) or status not in contract_statuses:
            failures.append(f"{label} has unsupported status {status!r}")

        comment = item.get("comment")
        if not isinstance(comment, str) or not comment.strip():
            failures.append(f"{label} comment must be a non-empty string")

        matrix_ids = item.get("matrix_ids")
        if not isinstance(matrix_ids, list):
            failures.append(f"{label} matrix_ids must be a list")
            continue
        if any(not isinstance(value, str) or not value.strip() for value in matrix_ids):
            failures.append(f"{label} matrix_ids must contain non-empty strings")
            continue
        if len(matrix_ids) != len(set(matrix_ids)):
            failures.append(f"{label} matrix_ids contains duplicates")
        mapped_matrix_ids.update(matrix_ids)

        if (
            isinstance(status, str)
            and status in {"aligned", "deviation"}
            and not matrix_ids
        ):
            failures.append(f"{label} status {status} requires at least one matrix_id")
        if status == "not_applicable" and matrix_ids:
            failures.append(f"{label} not_applicable requires empty matrix_ids")
        if status == "extra_in_contract" and matrix_ids:
            failures.append(f"{label} extra_in_contract requires empty matrix_ids")

    for contract_id, occurrences in contract_occurrences.items():
        if len(occurrences) < 2:
            continue
        locators = [locator for _, locator in occurrences]
        if any(locator is None for locator in locators):
            failures.append(
                f"duplicate contract_id {contract_id} requires source_locator "
                "for every occurrence"
            )
            continue
        if len(locators) != len(set(locators)):
            failures.append(
                f"duplicate contract_id {contract_id} requires unique source_locator values"
            )

    matrix_statuses = {"missing_in_contract", "needs_review"}
    matrix_ids_seen: set[str] = set()
    missing_matrix_ids: set[str] = set()
    matrix_keys = {"matrix_id", "status", "comment"}

    for index, item in enumerate(matrix_items):
        label = f"matrix_items[{index}]"
        if not isinstance(item, dict):
            failures.append(f"{label} must be an object")
            continue

        missing_keys = sorted(matrix_keys - item.keys())
        unexpected_keys = sorted(item.keys() - matrix_keys)
        if missing_keys:
            failures.append(f"{label} is missing keys: " + ", ".join(missing_keys))
        if unexpected_keys:
            failures.append(
                f"{label} has unsupported keys: " + ", ".join(unexpected_keys)
            )

        matrix_id = item.get("matrix_id")
        if not isinstance(matrix_id, str) or not matrix_id.strip():
            failures.append(f"{label} matrix_id must be a non-empty string")
        elif matrix_id in matrix_ids_seen:
            failures.append(f"{label} duplicates matrix_id {matrix_id}")
        else:
            matrix_ids_seen.add(matrix_id)

        status = item.get("status")
        if not isinstance(status, str) or status not in matrix_statuses:
            failures.append(f"{label} has unsupported status {status!r}")
        elif status == "missing_in_contract" and isinstance(matrix_id, str):
            missing_matrix_ids.add(matrix_id)

        comment = item.get("comment")
        if not isinstance(comment, str) or not comment.strip():
            failures.append(f"{label} comment must be a non-empty string")

    collisions = sorted(missing_matrix_ids & mapped_matrix_ids)
    if collisions:
        failures.append(
            "matrix IDs cannot be both mapped and missing_in_contract: "
            + ", ".join(collisions)
        )

    return failures


def _invoke_with_transient_retries(
    agent,
    prompt: str,
    config: dict,
    *,
    max_retries: int,
    sleep,
) -> None:
    retryable_errors = (
        httpx.TransportError,
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.InternalServerError,
        openai.RateLimitError,
    )
    for attempt in range(max_retries + 1):
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config=config,
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
        except openai.APIStatusError as exc:
            raise RuntimeError(
                f"Agent API request failed with status {exc.status_code}: {exc.message}"
            ) from exc


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
    config = {
        "configurable": {"thread_id": stable_thread_id},
        "recursion_limit": 10_000,
        "callbacks": [CompactTraceHandler(workspace / TRACE_ARTIFACT)],
    }

    _invoke_with_transient_retries(
        agent,
        RUN_PROMPT,
        config,
        max_retries=max_retries,
        sleep=sleep,
    )
    failures = quality_gate_failures(workspace)
    if failures:
        raise RuntimeError(
            "Agent result failed structural validation: " + "; ".join(failures)
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _published_trace_path(result_output: Path) -> Path:
    return result_output.with_name(f"{result_output.stem}.trace.jsonl")


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

        gate_failures = quality_gate_failures(workspace)
        if gate_failures:
            raise RuntimeError(
                "Agent output failed the publication gate: "
                + "; ".join(gate_failures)
            )
        result = workspace / RESULT_ARTIFACT
        shutil.copy2(result, result_output)
        trace_output = _published_trace_path(result_output)
        shutil.copy2(workspace / TRACE_ARTIFACT, trace_output)
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
    print(_published_trace_path(result_output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
