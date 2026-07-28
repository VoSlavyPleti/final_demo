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

RESULT_ARTIFACT = Path("outputs/result.json")
STATUS_AUDIT_ARTIFACT = Path("outputs/working/status-audit.json")
COVERAGE_AUDIT_ARTIFACT = Path("outputs/working/coverage-audit.json")
RUN_MANIFEST = Path("outputs/run-manifest.json")


def _load_prompt(name: str) -> str:
    return (PROMPTS_ROOT / name).read_text(encoding="utf-8").strip()


AGENT_SYSTEM_PROMPT = _load_prompt("orchestrator-system.md")
RUN_PROMPT = _load_prompt("contract-review-user.md")
QUALITY_REPAIR_PROMPT = _load_prompt("quality-repair-user.md")

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
        help="Maximum API retries and quality-gate repair passes; default: 3",
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
    """Check only artifact readiness; legal judgments remain agent-owned."""

    failures: list[str] = []
    result, errors = _read_json_object(workspace / RESULT_ARTIFACT, "result")
    failures.extend(errors)
    audit, errors = _read_json_object(
        workspace / COVERAGE_AUDIT_ARTIFACT,
        "coverage audit",
    )
    failures.extend(errors)
    status_audit, errors = _read_json_object(
        workspace / STATUS_AUDIT_ARTIFACT,
        "status audit",
    )
    failures.extend(errors)
    if result is None or audit is None or status_audit is None:
        return failures

    required_result_keys = {
        "schema_version",
        "completion_status",
        "contract_items",
        "matrix_items",
        "review_items",
    }
    missing_result_keys = sorted(required_result_keys - result.keys())
    if missing_result_keys:
        failures.append(
            "result is missing top-level keys: " + ", ".join(missing_result_keys)
        )
    if result.get("completion_status") not in {"complete", "complete_with_review"}:
        failures.append("result completion_status is not complete")

    contract_items = result.get("contract_items")
    if not isinstance(contract_items, list):
        failures.append("result contract_items must be a list")
        contract_items = None
    matrix_items = result.get("matrix_items")
    if not isinstance(matrix_items, list):
        failures.append("result matrix_items must be a list")
        matrix_items = None

    required_audit_keys = {
        "schema_version",
        "completion_status",
        "source_contract_item_count",
        "result_contract_item_count",
        "source_contract_ids",
        "result_contract_ids",
        "contract_inventory_complete",
        "all_contract_items_processed",
        "mandatory_matrix_sweep_complete",
        "business_aligned_challenge_complete",
        "business_deviation_sweep_complete",
        "suppression_sweep_complete",
        "main_idea_evidence_check_complete",
        "status_audit_complete",
        "number_neutrality_review_complete",
        "mapping_cliff_review_complete",
        "unprocessed_contract_ids",
        "duplicate_contract_ids",
        "synthetic_contract_ids",
        "unresolved_sections",
        "blocker_count",
        "blockers",
    }
    missing_audit_keys = sorted(required_audit_keys - audit.keys())
    if missing_audit_keys:
        failures.append(
            "coverage audit is missing keys: " + ", ".join(missing_audit_keys)
        )
        return failures

    if audit.get("schema_version") != "contract-review-coverage.v2":
        failures.append("coverage audit schema_version is unsupported")
    if audit.get("completion_status") != "complete":
        failures.append("coverage audit completion_status is not complete")

    for field in (
        "contract_inventory_complete",
        "all_contract_items_processed",
        "mandatory_matrix_sweep_complete",
        "business_aligned_challenge_complete",
        "business_deviation_sweep_complete",
        "suppression_sweep_complete",
        "main_idea_evidence_check_complete",
        "status_audit_complete",
        "number_neutrality_review_complete",
        "mapping_cliff_review_complete",
    ):
        if audit.get(field) is not True:
            failures.append(f"coverage audit {field} is not true")

    for field in (
        "unprocessed_contract_ids",
        "duplicate_contract_ids",
        "synthetic_contract_ids",
        "unresolved_sections",
        "blockers",
    ):
        value = audit.get(field)
        if not isinstance(value, list):
            failures.append(f"coverage audit {field} must be a list")
        elif value:
            failures.append(f"coverage audit {field} is not empty")

    blocker_count = audit.get("blocker_count")
    if isinstance(blocker_count, bool) or not isinstance(blocker_count, int):
        failures.append("coverage audit blocker_count must be an integer")
    elif blocker_count != 0:
        failures.append("coverage audit blocker_count is not zero")

    source_count = audit.get("source_contract_item_count")
    result_count = audit.get("result_contract_item_count")
    for field, value in (
        ("source_contract_item_count", source_count),
        ("result_contract_item_count", result_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            failures.append(f"coverage audit {field} must be a non-negative integer")
    if isinstance(source_count, int) and isinstance(result_count, int):
        if source_count != result_count:
            failures.append("coverage audit source and result counts differ")
        if contract_items is not None and result_count != len(contract_items):
            failures.append(
                "coverage audit result count differs from result contract_items length"
            )

    source_ids = audit.get("source_contract_ids")
    result_ids = audit.get("result_contract_ids")
    if not isinstance(source_ids, list):
        failures.append("coverage audit source_contract_ids must be a list")
    if not isinstance(result_ids, list):
        failures.append("coverage audit result_contract_ids must be a list")
    if isinstance(source_ids, list) and isinstance(result_ids, list):
        if source_ids != result_ids:
            failures.append("coverage audit source and result ID manifests differ")
        if len(source_ids) != len(set(map(str, source_ids))):
            failures.append("coverage audit source_contract_ids contains duplicates")
        if len(result_ids) != len(set(map(str, result_ids))):
            failures.append("coverage audit result_contract_ids contains duplicates")
        if isinstance(source_count, int) and len(source_ids) != source_count:
            failures.append(
                "coverage audit source ID manifest length differs from source count"
            )
        if isinstance(result_count, int) and len(result_ids) != result_count:
            failures.append(
                "coverage audit result ID manifest length differs from result count"
            )
        if contract_items is not None:
            artifact_ids = [
                item.get("contract_id") if isinstance(item, dict) else None
                for item in contract_items
            ]
            if result_ids != artifact_ids:
                failures.append(
                    "coverage audit result ID manifest differs from result order"
                )

    required_status_audit_keys = {
        "schema_version",
        "completion_status",
        "deviation_decisions",
        "extra_decisions",
        "missing_decisions",
        "rejected_deviation_candidates",
        "blocker_count",
        "blockers",
    }
    missing_status_audit_keys = sorted(
        required_status_audit_keys - status_audit.keys()
    )
    if missing_status_audit_keys:
        failures.append(
            "status audit is missing keys: "
            + ", ".join(missing_status_audit_keys)
        )
        return failures

    if status_audit.get("schema_version") != "contract-review-status-audit.v1":
        failures.append("status audit schema_version is unsupported")
    if status_audit.get("completion_status") != "complete":
        failures.append("status audit completion_status is not complete")

    decisions = status_audit.get("deviation_decisions")
    if not isinstance(decisions, list):
        failures.append("status audit deviation_decisions must be a list")
    else:
        required_decision_keys = {
            "contract_id",
            "matrix_ids",
            "business_category",
            "shared_business_proposition",
            "delta",
            "bank_impact",
            "matrix_evidence",
            "contract_evidence",
            "suppression_checks",
            "decision",
        }
        for index, decision in enumerate(decisions):
            label = f"status audit deviation_decisions[{index}]"
            if not isinstance(decision, dict):
                failures.append(f"{label} must be an object")
                continue
            missing_keys = sorted(required_decision_keys - decision.keys())
            if missing_keys:
                failures.append(
                    f"{label} is missing keys: " + ", ".join(missing_keys)
                )
            if not isinstance(decision.get("matrix_ids"), list):
                failures.append(f"{label} matrix_ids must be a list")
            if not isinstance(decision.get("suppression_checks"), dict):
                failures.append(f"{label} suppression_checks must be an object")
            if decision.get("decision") != "deviation":
                failures.append(f"{label} decision must be deviation")

    if not isinstance(status_audit.get("rejected_deviation_candidates"), list):
        failures.append(
            "status audit rejected_deviation_candidates must be a list"
        )
    extra_decisions = status_audit.get("extra_decisions")
    if not isinstance(extra_decisions, list):
        failures.append("status audit extra_decisions must be a list")
    else:
        required_extra_keys = {
            "contract_id",
            "candidate_matrix_ids_checked",
            "operational_effect",
            "no_shared_business_proposition_reason",
            "decision",
        }
        for index, decision in enumerate(extra_decisions):
            label = f"status audit extra_decisions[{index}]"
            if not isinstance(decision, dict):
                failures.append(f"{label} must be an object")
                continue
            missing_keys = sorted(required_extra_keys - decision.keys())
            if missing_keys:
                failures.append(
                    f"{label} is missing keys: " + ", ".join(missing_keys)
                )
            if not isinstance(decision.get("candidate_matrix_ids_checked"), list):
                failures.append(
                    f"{label} candidate_matrix_ids_checked must be a list"
                )
            if decision.get("decision") != "extra_in_contract":
                failures.append(f"{label} decision must be extra_in_contract")
    missing_decisions = status_audit.get("missing_decisions")
    if not isinstance(missing_decisions, list):
        failures.append("status audit missing_decisions must be a list")
    else:
        required_missing_keys = {
            "matrix_id",
            "semantic_candidates_checked",
            "same_relationship_partial_analog_found",
            "applicability_basis",
            "no_analog_reason",
            "decision",
        }
        for index, decision in enumerate(missing_decisions):
            label = f"status audit missing_decisions[{index}]"
            if not isinstance(decision, dict):
                failures.append(f"{label} must be an object")
                continue
            missing_keys = sorted(required_missing_keys - decision.keys())
            if missing_keys:
                failures.append(
                    f"{label} is missing keys: " + ", ".join(missing_keys)
                )
            if not isinstance(decision.get("semantic_candidates_checked"), list):
                failures.append(
                    f"{label} semantic_candidates_checked must be a list"
                )
            if decision.get("same_relationship_partial_analog_found") is not False:
                failures.append(
                    f"{label} cannot publish missing after finding a partial analog"
                )
            if decision.get("decision") != "missing_in_contract":
                failures.append(f"{label} decision must be missing_in_contract")
    status_blockers = status_audit.get("blockers")
    if not isinstance(status_blockers, list):
        failures.append("status audit blockers must be a list")
    elif status_blockers:
        failures.append("status audit blockers is not empty")
    status_blocker_count = status_audit.get("blocker_count")
    if (
        isinstance(status_blocker_count, bool)
        or not isinstance(status_blocker_count, int)
    ):
        failures.append("status audit blocker_count must be an integer")
    elif status_blocker_count != 0:
        failures.append("status audit blocker_count is not zero")

    if contract_items is not None:
        published_deviation_ids = [
            item.get("contract_id")
            for item in contract_items
            if isinstance(item, dict) and item.get("status") == "deviation"
        ]
        audited_deviation_ids = (
            [
                item.get("contract_id")
                for item in decisions
                if isinstance(item, dict)
            ]
            if isinstance(decisions, list)
            else []
        )
        if audited_deviation_ids != published_deviation_ids:
            failures.append(
                "status audit deviation decisions differ from published deviations"
            )

        published_extra_ids = [
            item.get("contract_id")
            for item in contract_items
            if isinstance(item, dict)
            and item.get("status") == "extra_in_contract"
        ]
        audited_extra_ids = (
            [
                item.get("contract_id")
                for item in extra_decisions
                if isinstance(item, dict)
            ]
            if isinstance(extra_decisions, list)
            else []
        )
        if audited_extra_ids != published_extra_ids:
            failures.append(
                "status audit extra decisions differ from published extras"
            )

    if matrix_items is not None:
        published_missing_ids = [
            item.get("matrix_id")
            for item in matrix_items
            if isinstance(item, dict)
            and item.get("status") == "missing_in_contract"
        ]
        audited_missing_ids = (
            [
                item.get("matrix_id")
                for item in missing_decisions
                if isinstance(item, dict)
            ]
            if isinstance(missing_decisions, list)
            else []
        )
        if audited_missing_ids != published_missing_ids:
            failures.append(
                "status audit missing decisions differ from published missing"
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
        "callbacks": [CompactTraceHandler()],
    }

    prompt = RUN_PROMPT
    for quality_attempt in range(max_retries + 1):
        _invoke_with_transient_retries(
            agent,
            prompt,
            config,
            max_retries=max_retries,
            sleep=sleep,
        )
        failures = quality_gate_failures(workspace)
        if not failures:
            return
        if quality_attempt >= max_retries:
            raise RuntimeError(
                "Agent stopped before the quality gate passed: "
                + "; ".join(failures)
            )
        print(
            "Quality gate not passed; continuing the same thread "
            f"[repair {quality_attempt + 1}/{max_retries}]",
            file=sys.stderr,
            flush=True,
        )
        prompt = QUALITY_REPAIR_PROMPT.format(
            failures="\n".join(f"- {failure}" for failure in failures)
        )


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

        gate_failures = quality_gate_failures(workspace)
        if gate_failures:
            raise RuntimeError(
                "Agent output failed the publication gate: "
                + "; ".join(gate_failures)
            )
        result = workspace / RESULT_ARTIFACT
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
