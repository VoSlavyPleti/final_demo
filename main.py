from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from typing import Any, Callable

import httpx
from gigachat.exceptions import ResponseError as GigaChatResponseError
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import MemorySaver

from llm import MODEL_REQUEST_TIMEOUT, close_llm, get_llm, set_llm_timeout
from console_logging import ConsoleLogHandler


PROJECT_ROOT = Path(__file__).resolve().parent
DOMAIN_SKILL_SOURCE = PROJECT_ROOT / "skills" / "contract-matrix-review"
SKILL_SOURCES = (DOMAIN_SKILL_SOURCE,)
PROMPTS_ROOT = PROJECT_ROOT / "prompts"
PYTHON_RUNTIME_ROOT = PROJECT_ROOT / "harness_runtime"

RESULT_ARTIFACT = Path("outputs/result.json")
RUN_MANIFEST = Path("outputs/run-manifest.json")
TRACE_ARTIFACT = Path("outputs/run-trace.jsonl")

AEF_RUNTIME_PROMPT = """

# Среда выполнения

Команды `execute` выполняются POSIX shell внутри изолированной WorkStation.
Рабочая директория — корень workspace. Используй относительные пути
`inputs/...`, `outputs/...` и `skills/...`; Windows-пути недоступны.
""".strip()


def _load_prompt(name: str) -> str:
    return (PROMPTS_ROOT / name).read_text(encoding="utf-8").strip()


AGENT_SYSTEM_PROMPT = _load_prompt("orchestrator-system.md")
RUN_PROMPT = _load_prompt("contract-review-user.md")

class CompactTraceHandler(BaseCallbackHandler):
    """Persist timing and payload fingerprints without storing source text."""

    run_inline = True
    _VIRTUAL_PATH = re.compile(
        r"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z]:)?[/\\]?"
        r"(?:inputs|outputs|skills|tmp|large_tool_results|"
        r"conversation_history|\.harness_runtime)(?:[/\\][\w.()-]+)+"
    )
    _EXTERNAL_FIELDS = {
        "event",
        "timestamp",
        "run_id",
        "attempt_id",
        "attempt_no",
        "thread_id",
        "session_id",
        "operation_id",
        "request_id",
        "trace_id",
        "span_id",
        "endpoint_template",
        "endpoint",
        "method",
        "http_status",
        "service_error_code",
        "duration_ms",
        "duration_seconds",
        "retry",
        "backoff_seconds",
        "backoff_ms",
        "path",
        "virtual_path",
        "bytes",
        "sha256",
        "command_sha256",
        "command_hash",
        "command_length",
        "timeout_seconds",
        "timeout_sec",
        "exit_code",
        "truncated",
        "ttl_remaining_seconds",
        "ttl_remaining_sec",
        "tainted",
        "orphan",
        "restart_reason",
        "cleanup_reason",
        "cleanup_status",
        "gate_status",
        "publication_status",
        "status",
        "error_type",
    }

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
        self._write_payload(payload)

    def _write_payload(self, payload: dict) -> None:
        serialized = json.dumps(payload, ensure_ascii=True)
        with self._lock:
            print("[trace] " + serialized, flush=True)
            if self._trace_path is not None:
                with self._trace_path.open("a", encoding="utf-8") as stream:
                    stream.write(serialized + "\n")

    def emit_metadata(self, payload: dict) -> None:
        """Append allow-listed infrastructure metadata to the same trace."""

        safe = {
            key: value
            for key, value in payload.items()
            if key in self._EXTERNAL_FIELDS
        }
        safe.setdefault("event", "aef_event")
        safe.setdefault("timestamp", round(time.time(), 3))
        self._write_payload(safe)

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


class AttemptDeadlineHandler(BaseCallbackHandler):
    """Prevent new model/tool work after an AEF attempt becomes unsafe."""

    run_inline = True
    raise_error = True

    def __init__(
        self,
        check: Callable[[], Any],
        *,
        model: Any | None = None,
        maximum_model_call_sec: float = MODEL_REQUEST_TIMEOUT,
    ) -> None:
        self._check = check
        self._model = model
        self._maximum_model_call_sec = maximum_model_call_sec
        self._timeout_lock = threading.Lock()
        self._watchdogs: dict[
            str,
            tuple[threading.Event, threading.Thread],
        ] = {}

    def _before_model(self, run_id: Any = None) -> None:
        remaining = self._check()
        if not isinstance(remaining, (int, float)) or self._model is None:
            return
        allowed = min(self._maximum_model_call_sec, max(0.1, float(remaining) - 0.25))
        # Bound the GigaChat SDK/transport by the session's remaining window.
        # The watchdog also observes shortened deadlines during silent calls.
        with self._timeout_lock:
            set_llm_timeout(self._model, allowed)

            key = str(run_id) if run_id is not None else uuid.uuid4().hex
            if key not in self._watchdogs:
                stop = threading.Event()
                watchdog = threading.Thread(
                    target=self._watch_model_call,
                    args=(key, stop),
                    name=f"aef-model-deadline-{key}",
                    daemon=True,
                )
                self._watchdogs[key] = (stop, watchdog)
                watchdog.start()

    def _watch_model_call(self, key: str, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                remaining = float(self._check())
            except BaseException:
                remaining = 0.0
            if remaining <= 0:
                break
            # Polling is local (no HTTP): it notices both a shortened
            # expiresAt and a heartbeat failure while a stream is silent.
            if stop.wait(min(0.1, remaining)):
                return
        with self._timeout_lock:
            active = self._watchdogs.pop(key, None)
        if active is not None and not stop.is_set():
            # httpx timeouts are per phase, not wall-clock. Closing the model's
            # dedicated sync client is the hard stop for a silent stream at the
            # absolute AEF attempt deadline. A fresh model is created if the
            # supervisor restarts the attempt.
            try:
                close_llm(self._model)
            except BaseException:
                pass

    def _cancel_timer(self, run_id: Any) -> None:
        key = str(run_id) if run_id is not None else None
        if key is None:
            return
        with self._timeout_lock:
            active = self._watchdogs.pop(key, None)
        if active is not None:
            active[0].set()

    def cancel_all(self) -> None:
        with self._timeout_lock:
            active = tuple(self._watchdogs.values())
            self._watchdogs.clear()
        for stop, _watchdog in active:
            stop.set()

    def on_chat_model_start(self, *args, **kwargs) -> None:
        del args
        self._before_model(kwargs.get("run_id"))

    def on_llm_start(self, *args, **kwargs) -> None:
        del args
        self._before_model(kwargs.get("run_id"))

    def on_llm_new_token(self, *args, **kwargs) -> None:
        del args
        self._check()

    def on_llm_end(self, *args, **kwargs) -> None:
        del args
        self._cancel_timer(kwargs.get("run_id"))
        self._check()

    def on_llm_error(self, *args, **kwargs) -> None:
        del args
        self._cancel_timer(kwargs.get("run_id"))
        self._check()

    def on_tool_start(self, *args, **kwargs) -> None:
        del args, kwargs
        self._check()


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
    parser.add_argument("--contract", type=Path, help="TXT path; default: AGENT_CONTRACT_PATH")
    parser.add_argument("--matrix", type=Path, help="JSON path; default: AGENT_MATRIX_PATH")
    parser.add_argument(
        "--output",
        type=Path,
        help="Full mapping JSON; default: AGENT_OUTPUT_PATH",
    )
    parser.add_argument(
        "--backend",
        choices=("aef", "local"),
        default=None,
        help=(
            "Execution backend. Default comes from AGENT_BACKEND and is aef; "
            "local is an explicit pre-run rollback only."
        ),
    )
    parser.add_argument(
        "--console-log",
        choices=("full", "compact"),
        help="Console content logging; default: AGENT_CONSOLE_LOG or full",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Preserve the local control workspace after a successful run",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum retries for transient API failures; default: 3",
    )
    parser.add_argument(
        "--max-infra-restarts",
        type=int,
        default=1,
        help="Maximum full AEF attempt restarts; default: 1",
    )
    args = parser.parse_args(argv)
    for name in ("contract", "matrix", "output"):
        if getattr(args, name) is None:
            variable = f"AGENT_{name.upper()}_PATH"
            value = os.environ.get(variable, "").strip()
            if not value:
                parser.error(f"Provide --{name} or set {variable} in .env")
            path = Path(value).expanduser()
            # Environment paths are stable regardless of the launch directory.
            # Explicit relative CLI paths retain normal cwd-relative semantics.
            setattr(args, name, path if path.is_absolute() else PROJECT_ROOT / path)
    if args.console_log is None:
        args.console_log = os.environ.get("AGENT_CONSOLE_LOG", "full").strip().lower()
        if args.console_log not in {"full", "compact"}:
            parser.error("AGENT_CONSOLE_LOG must be either 'full' or 'compact'")
    if args.backend is None:
        configured_backend = os.environ.get("AGENT_BACKEND", "aef").strip().lower()
        if configured_backend not in {"aef", "local"}:
            parser.error("AGENT_BACKEND must be either 'aef' or 'local'")
        args.backend = configured_backend
    return args


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


def _same_file_or_path(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _read_nonempty_utf8(path: Path, label: str) -> str:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8: {path}") from exc
    if not text.strip():
        raise ValueError(f"{label} must not be empty: {path}")
    return text


def _validate_local_sources(contract: Path, matrix: Path) -> None:
    """Fail before workspace/session creation when immutable sources are invalid."""

    _read_nonempty_utf8(contract, "Contract")
    matrix_text = _read_nonempty_utf8(matrix, "Matrix")
    try:
        matrix_payload = json.loads(matrix_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Matrix must be valid JSON: {matrix}") from exc
    if not isinstance(matrix_payload, list) or not matrix_payload:
        raise ValueError("Matrix JSON must be a non-empty array")
    if any(not isinstance(item, dict) for item in matrix_payload):
        raise ValueError("Every Matrix JSON array item must be an object")

    required_skill_files = (
        DOMAIN_SKILL_SOURCE / "SKILL.md",
        DOMAIN_SKILL_SOURCE / "references" / "business-deviation-policy.md",
        DOMAIN_SKILL_SOURCE / "references" / "business-casebook.md",
        DOMAIN_SKILL_SOURCE / "references" / "output-schema.md",
    )
    for path in required_skill_files:
        if not path.is_file():
            raise FileNotFoundError(f"Required agent file does not exist: {path}")
    skill_text = _read_nonempty_utf8(required_skill_files[0], "Agent skill")
    if not skill_text.startswith("---\n"):
        raise ValueError("Agent SKILL.md must start with YAML frontmatter")
    frontmatter_end = skill_text.find("\n---", 4)
    if frontmatter_end < 0:
        raise ValueError("Agent SKILL.md has unterminated YAML frontmatter")
    frontmatter = skill_text[4:frontmatter_end]
    for field in ("name", "description"):
        match = re.search(rf"(?m)^{field}:\s*(.+?)\s*$", frontmatter)
        if match is None or not match.group(1).strip(" \t\"'"):
            raise ValueError(f"Agent SKILL.md frontmatter requires non-empty {field}")
    for path in DOMAIN_SKILL_SOURCE.rglob("*.md"):
        _read_nonempty_utf8(path, "Agent skill resource")

    runtime = PYTHON_RUNTIME_ROOT / "sitecustomize.py"
    if not runtime.is_file():
        raise FileNotFoundError(f"Required Python runtime file does not exist: {runtime}")
    runtime_text = _read_nonempty_utf8(runtime, "Python runtime")
    try:
        compile(runtime_text, str(runtime), "exec")
    except SyntaxError as exc:
        raise ValueError(f"Python runtime is not syntactically valid: {runtime}") from exc


def prepare_workspace(
    contract: Path, matrix: Path, output: Path
) -> tuple[Path, Path, Path, Path]:
    contract = _resolve_input(contract, "Contract")
    matrix = _resolve_input(matrix, "Matrix")
    if contract.suffix.lower() != ".txt":
        raise ValueError(f"Contract must be a .txt file: {contract}")
    if matrix.suffix.lower() != ".json":
        raise ValueError(f"Matrix must be a .json file: {matrix}")
    _validate_local_sources(contract, matrix)
    output = _resolve_json_output(output, "Output")
    publication_targets = (
        output,
        _published_trace_path(output),
        _published_manifest_path(output),
    )
    if any(
        _same_file_or_path(target, source)
        for target in publication_targets
        for source in (contract, matrix)
    ):
        raise ValueError(
            "Result, trace and manifest paths must differ from both input paths"
        )

    workspace = Path(tempfile.mkdtemp(prefix="contract-review-"))
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs" / "working").mkdir(parents=True)
    shutil.copytree(
        DOMAIN_SKILL_SOURCE,
        workspace / "skills" / DOMAIN_SKILL_SOURCE.name,
    )
    shutil.copytree(PYTHON_RUNTIME_ROOT, workspace / ".harness_runtime")
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
    backend: Any,
    *,
    checkpointer=None,
    system_prompt: str | None = None,
    model=None,
):
    selected_model = model or get_llm()
    return create_deep_agent(
        name="contract-matrix-review-agent",
        model=selected_model,
        system_prompt=system_prompt or AGENT_SYSTEM_PROMPT,
        backend=backend,
        skills=["/skills/"],
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
    before_retry: Callable[[], None] | None = None,
) -> None:
    for attempt in range(max_retries + 1):
        try:
            agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config=config,
            )
            return
        except (httpx.TransportError, GigaChatResponseError) as exc:
            if isinstance(exc, GigaChatResponseError) and exc.status_code not in (
                408, 429, 500, 502, 503, 504,
            ):
                # SDK exception strings contain raw headers/body; do not log them.
                raise RuntimeError(
                    f"Agent API request failed with status {exc.status_code}"
                ) from None
            if attempt >= max_retries:
                raise RuntimeError(
                    f"Agent failed after {max_retries} transient retries"
                ) from None
            if before_retry is not None:
                before_retry()
            delay_seconds = min(2 ** (attempt + 1), 30)
            print(
                f"Transient failure ({type(exc).__name__}); continuing the "
                f"same thread in {delay_seconds}s "
                f"[retry {attempt + 1}/{max_retries}]",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay_seconds)


def _invoke_agent(
    workspace: Path,
    backend: Any,
    *,
    max_retries: int = 3,
    thread_id: str | None = None,
    sleep=time.sleep,
    trace_handler: CompactTraceHandler | None = None,
    before_retry: Callable[[], None] | None = None,
    attempt_check: Callable[[], Any] | None = None,
    system_prompt: str | None = None,
    model=None,
    console_log: str = "full",
) -> str:
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    agent_kwargs: dict[str, Any] = {"checkpointer": MemorySaver()}
    if system_prompt is not None:
        agent_kwargs["system_prompt"] = system_prompt
    if model is not None:
        agent_kwargs["model"] = model
    agent = build_agent(backend, **agent_kwargs)
    stable_thread_id = thread_id or uuid.uuid4().hex
    callbacks: list[BaseCallbackHandler] = [
        trace_handler or CompactTraceHandler(workspace / TRACE_ARTIFACT)
    ]
    if console_log not in {"full", "compact"}:
        raise ValueError("console_log must be either 'full' or 'compact'")
    if console_log == "full":
        callbacks.append(ConsoleLogHandler())
    deadline_handler: AttemptDeadlineHandler | None = None
    if attempt_check is not None:
        deadline_handler = AttemptDeadlineHandler(attempt_check, model=model)
        callbacks.append(deadline_handler)
    config = {
        "configurable": {"thread_id": stable_thread_id},
        "recursion_limit": 10_000,
        "callbacks": callbacks,
    }

    try:
        _invoke_with_transient_retries(
            agent,
            RUN_PROMPT,
            config,
            max_retries=max_retries,
            sleep=sleep,
            before_retry=before_retry,
        )
    finally:
        if deadline_handler is not None:
            deadline_handler.cancel_all()
    return stable_thread_id


def run_agent(
    workspace: Path,
    *,
    max_retries: int = 3,
    thread_id: str | None = None,
    sleep=time.sleep,
    console_log: str = "full",
) -> None:
    _invoke_agent(
        workspace,
        build_backend(workspace),
        max_retries=max_retries,
        thread_id=thread_id,
        sleep=sleep,
        console_log=console_log,
    )
    failures = quality_gate_failures(workspace)
    if failures:
        raise RuntimeError(
            "Agent result failed structural validation: " + "; ".join(failures)
        )


def run_agent_aef(
    workspace: Path,
    *,
    settings,
    run_id: str,
    staging_snapshot: StagingSnapshot | None = None,
    max_retries: int = 3,
    max_infra_restarts: int = 1,
    attempt_reports_out: list[dict] | None = None,
    console_log: str = "full",
) -> tuple[str, list[dict]]:
    """Run one business analysis with attempt-scoped WorkStation sessions."""

    from aef_workstation import RunSupervisor

    if max_infra_restarts not in {0, 1}:
        raise ValueError("max_infra_restarts must be 0 or 1")

    trace_handler = CompactTraceHandler(workspace / TRACE_ARTIFACT)
    supervisor = RunSupervisor(
        settings,
        run_id=run_id,
        event_sink=trace_handler.emit_metadata,
        max_attempts=max_infra_restarts + 1,
        expected_manifest=(
            {
                entry.virtual_path: (entry.bytes, entry.sha256)
                for entry in staging_snapshot.entries
            }
            if staging_snapshot is not None
            else None
        ),
    )
    def attempt(manager, backend, thread_id: str, attempt_no: int) -> str:
        del attempt_no
        local_result = workspace / RESULT_ARTIFACT
        local_result.unlink(missing_ok=True)
        # Model retries belong to _invoke_with_transient_retries, where
        # session health is checked first. Disable the SDK's hidden retry loop
        # and create a fresh transport for every clean infrastructure attempt.
        model = get_llm(max_retries=0)
        try:
            manager.ensure_healthy()
            _invoke_agent(
                workspace,
                backend,
                max_retries=max_retries,
                thread_id=thread_id,
                trace_handler=trace_handler,
                before_retry=manager.ensure_healthy,
                attempt_check=getattr(
                    manager,
                    "check_attempt_active",
                    manager.ensure_healthy,
                ),
                system_prompt=f"{AGENT_SYSTEM_PROMPT}\n\n{AEF_RUNTIME_PROMPT}",
                model=model,
                console_log=console_log,
            )
            manager.ensure_healthy()
            manager.verify_integrity()
            result_bytes = manager.download_result()
            manager.ensure_healthy()
            manager.verify_integrity()
            _atomic_write_bytes(result_bytes, local_result)
            failures = quality_gate_failures(workspace)
            if failures:
                raise RuntimeError(
                    "Agent result failed structural validation: " + "; ".join(failures)
                )
            return thread_id
        finally:
            close_llm(model)

    try:
        final_thread_id = supervisor.run(workspace, attempt)
        return final_thread_id, list(supervisor.reports)
    finally:
        if attempt_reports_out is not None:
            attempt_reports_out[:] = supervisor.reports


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


@dataclass(frozen=True)
class StagingEntrySnapshot:
    """Immutable metadata for one file mounted into the agent workspace."""

    virtual_path: str
    bytes: int
    sha256: str

    def to_manifest(self) -> dict[str, str | int]:
        return {
            "virtual_path": self.virtual_path,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class StagingSnapshot:
    """Immutable fingerprint of the files actually staged for one run."""

    entries: tuple[StagingEntrySnapshot, ...]
    contract_sha256: str
    matrix_sha256: str
    skill_sha256: str
    runtime_sha256: str


def _snapshot_tree_sha256(
    entries: tuple[StagingEntrySnapshot, ...],
    virtual_root: str,
) -> str:
    prefix = virtual_root.rstrip("/") + "/"
    digest = hashlib.sha256()
    selected = sorted(
        (entry for entry in entries if entry.virtual_path.startswith(prefix)),
        key=lambda entry: entry.virtual_path,
    )
    for entry in selected:
        relative = entry.virtual_path.removeprefix(prefix).encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(entry.sha256))
    return digest.hexdigest()


def _capture_staging_snapshot(workspace: Path) -> StagingSnapshot:
    """Fingerprint mounted sources once, before an agent can mutate workspace."""

    mounted_roots = (
        workspace / "inputs",
        workspace / "skills",
        workspace / ".harness_runtime",
    )
    entries: list[StagingEntrySnapshot] = []
    for root in mounted_roots:
        if not root.is_dir():
            raise FileNotFoundError(f"Mounted staging root is missing: {root}")
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            content = path.read_bytes()
            entries.append(
                StagingEntrySnapshot(
                    virtual_path="/" + path.relative_to(workspace).as_posix(),
                    bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )

    frozen_entries = tuple(sorted(entries, key=lambda entry: entry.virtual_path))
    by_path = {entry.virtual_path: entry for entry in frozen_entries}
    required = {
        "/inputs/contract.txt",
        "/inputs/matrix.json",
        "/skills/contract-matrix-review/SKILL.md",
        "/.harness_runtime/sitecustomize.py",
    }
    missing = sorted(required.difference(by_path))
    if missing:
        raise FileNotFoundError(
            "Mounted staging snapshot is missing required files: "
            + ", ".join(missing)
        )

    return StagingSnapshot(
        entries=frozen_entries,
        contract_sha256=by_path["/inputs/contract.txt"].sha256,
        matrix_sha256=by_path["/inputs/matrix.json"].sha256,
        skill_sha256=_snapshot_tree_sha256(
            frozen_entries,
            "/skills/contract-matrix-review",
        ),
        runtime_sha256=_snapshot_tree_sha256(
            frozen_entries,
            "/.harness_runtime",
        ),
    )


def _published_trace_path(result_output: Path) -> Path:
    return result_output.with_name(f"{result_output.stem}.trace.jsonl")


def _published_manifest_path(result_output: Path) -> Path:
    return result_output.with_name(f"{result_output.stem}.manifest.json")


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_bytes(content: bytes, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(payload: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(
    workspace: Path,
    *,
    run_id: str,
    thread_id: str,
    status: str,
    staging_snapshot: StagingSnapshot,
    error: str | None = None,
    backend: str = "local",
    attempts: list[dict] | None = None,
    environment: str | None = None,
    endpoint: str | None = None,
    gate_status: str | None = None,
    publication_status: str | None = None,
) -> dict:
    result_path = workspace / RESULT_ARTIFACT
    trace_path = workspace / TRACE_ARTIFACT
    payload = {
        "schema_version": "contract-review-run.v2",
        "run_id": run_id,
        "thread_id": thread_id,
        "status": status,
        "backend": backend,
        "environment": environment,
        "endpoint": endpoint,
        "contract_sha256": staging_snapshot.contract_sha256,
        "matrix_sha256": staging_snapshot.matrix_sha256,
        "skill": "contract-matrix-review",
        "skill_sha256": staging_snapshot.skill_sha256,
        "runtime_sha256": staging_snapshot.runtime_sha256,
        "staging_entries": [
            entry.to_manifest() for entry in staging_snapshot.entries
        ],
        "result_sha256": (
            _sha256(result_path)
            if status == "complete" and result_path.is_file()
            else None
        ),
        "trace_sha256": _sha256(trace_path) if trace_path.is_file() else None,
        "attempts": attempts or [],
        "gate_status": gate_status,
        "publication_status": publication_status,
        "error": error,
    }
    target = workspace / RUN_MANIFEST
    _atomic_write_json(payload, target)
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    args = parse_args(argv)
    started = time.perf_counter()
    workspace: Path | None = None
    result_output: Path | None = None
    staging_snapshot: StagingSnapshot | None = None
    completed = False
    run_id = uuid.uuid4().hex
    thread_id = uuid.uuid4().hex
    attempt_reports: list[dict] = []
    backend_environment: str | None = None
    backend_endpoint: str | None = None

    try:
        if args.max_retries < 0:
            raise ValueError("--max-retries must be non-negative")
        if args.max_infra_restarts not in {0, 1}:
            raise ValueError("--max-infra-restarts must be 0 or 1")
        workspace, result_output, contract, matrix = prepare_workspace(
            args.contract,
            args.matrix,
            args.output,
        )
        print(
            f"[agent] backend={args.backend} console_log={args.console_log}\n"
            f"[agent] contract={contract}\n[agent] matrix={matrix}\n"
            f"[agent] output={result_output}",
            flush=True,
        )
        if args.console_log == "full":
            print("[agent] Full console log includes confidential document content.", flush=True)
        staging_snapshot = _capture_staging_snapshot(workspace)
        _write_manifest(
            workspace,
            run_id=run_id,
            thread_id=thread_id,
            status="in_progress",
            staging_snapshot=staging_snapshot,
            backend=args.backend,
            environment=backend_environment,
            endpoint=backend_endpoint,
            gate_status="pending",
            publication_status="pending",
        )

        if args.backend == "aef":
            from aef_workstation import AefSettings

            settings = AefSettings.from_env()
            backend_environment = settings.environment
            backend_endpoint = settings.base_url
            _write_manifest(
                workspace,
                run_id=run_id,
                thread_id=thread_id,
                status="in_progress",
                staging_snapshot=staging_snapshot,
                backend="aef",
                environment=backend_environment,
                endpoint=backend_endpoint,
                gate_status="pending",
                publication_status="pending",
            )
            thread_id, attempt_reports = run_agent_aef(
                workspace,
                settings=settings,
                run_id=run_id,
                staging_snapshot=staging_snapshot,
                max_retries=args.max_retries,
                max_infra_restarts=args.max_infra_restarts,
                attempt_reports_out=attempt_reports,
                console_log=args.console_log,
            )
        elif args.backend == "local":
            local_started = time.monotonic()
            run_agent(
                workspace,
                max_retries=args.max_retries,
                thread_id=thread_id,
                console_log=args.console_log,
            )
            attempt_reports = [
                {
                    "attempt_no": 1,
                    "thread_id": thread_id,
                    "session_id": None,
                    "status": "complete",
                    "restart_reason": None,
                    "cleanup_status": "complete",
                    "cleanup_duration_ms": 0,
                    "duration_ms": round((time.monotonic() - local_started) * 1000),
                }
            ]
        else:
            raise ValueError(f"Unsupported backend: {args.backend}")

        gate_failures = quality_gate_failures(workspace)
        if gate_failures:
            raise RuntimeError(
                "Agent output failed the publication gate: "
                + "; ".join(gate_failures)
            )
        result = workspace / RESULT_ARTIFACT
        trace_output = _published_trace_path(result_output)
        manifest_output = _published_manifest_path(result_output)
        manifest_payload = _write_manifest(
            workspace,
            run_id=run_id,
            thread_id=thread_id,
            status="complete",
            staging_snapshot=staging_snapshot,
            backend=args.backend,
            attempts=attempt_reports,
            environment=backend_environment,
            endpoint=backend_endpoint,
            gate_status="passed",
            publication_status="complete",
        )
        _atomic_copy(result, result_output)
        _atomic_copy(workspace / TRACE_ARTIFACT, trace_output)
        _atomic_write_json(manifest_payload, manifest_output)
        completed = True
    except (OSError, RuntimeError, ValueError) as exc:
        if (
            workspace is not None
            and result_output is not None
            and staging_snapshot is not None
        ):
            try:
                trace = workspace / TRACE_ARTIFACT
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.touch(exist_ok=True)
                failed_manifest = _write_manifest(
                    workspace,
                    run_id=run_id,
                    thread_id=thread_id,
                    status="failed",
                    staging_snapshot=staging_snapshot,
                    error=type(exc).__name__,
                    backend=args.backend,
                    attempts=attempt_reports,
                    environment=backend_environment,
                    endpoint=backend_endpoint,
                    gate_status="failed",
                    publication_status="diagnostics_published",
                )
                _atomic_copy(trace, _published_trace_path(result_output))
                _atomic_write_json(
                    failed_manifest,
                    _published_manifest_path(result_output),
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
