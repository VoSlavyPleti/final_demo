"""AEF WorkStation transport and Deep Agents sandbox backend.

This module deliberately keeps transport, lifecycle, and virtual-filesystem
policy outside the legal-analysis prompts and skills.  A WorkStation session is
an attempt-scoped sandbox: an ambiguous arbitrary command invalidates the whole
attempt because the unchanged service cannot provide exactly-once execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import base64
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any, Generic, TypeVar
from urllib.parse import urlsplit

import httpx
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.filesystem import _get_file_type
from deepagents.backends.utils import (
    create_file_data,
    grep_matches_from_files,
    perform_string_replacement,
    slice_read_response,
    validate_path,
)
from wcmatch import glob as wcglob


TEST_WORKSTATION_URL = "https://workstation.dev0.apps.azwx5oj1.k8s.delta.sbrf.ru"
_WORKSPACE_ROOT = "/workspace"
_PROTECTED_ROOTS = ("/inputs", "/skills", "/.harness_runtime")
_SAFE_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_TOOL_ERROR_STATUSES = frozenset({400, 403, 413, 422})
_CREATE_AMBIGUOUS_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_SAFE_LOG_FIELDS = frozenset(
    {
        "event",
        "timestamp",
        "run_id",
        "attempt_no",
        "attempt_id",
        "thread_id",
        "session_id",
        "operation_id",
        "request_id",
        "trace_id",
        "span_id",
        "endpoint",
        "method",
        "http_status",
        "service_error_code",
        "duration_ms",
        "retry",
        "backoff_ms",
        "virtual_path",
        "bytes",
        "sha256",
        "command_hash",
        "command_length",
        "timeout_sec",
        "exit_code",
        "truncated",
        "ttl_remaining_sec",
        "status",
        "tainted",
        "orphan",
        "restart_reason",
        "cleanup_reason",
        "cleanup_status",
        "gate_status",
        "publication_status",
        "error_type",
    }
)


class AefError(RuntimeError):
    """Base exception for the AEF integration."""


class AefConfigurationError(AefError):
    """Local configuration is unsafe or incompatible."""


class AefInfrastructureError(AefError):
    """The WorkStation endpoint or session is unavailable."""


class AefProtocolError(AefError):
    """The service returned a response that violates its documented schema."""


class AefPolicyError(AefError):
    """A requested operation violates the harness filesystem policy."""


class AefToolError(AefError):
    """A service rejection that should be exposed as a tool result."""

    def __init__(self, status_code: int, service_code: str | None = None) -> None:
        self.status_code = status_code
        self.service_code = service_code
        suffix = f" ({service_code})" if service_code else ""
        super().__init__(f"WorkStation rejected the operation with HTTP {status_code}{suffix}")


class AefAttemptLost(RuntimeError):
    """The attempt is tainted and must restart in a new WorkStation session.

    This intentionally does not inherit ``httpx.TransportError``, ``ValueError``,
    or ``NotImplementedError``.  Deep Agents must not turn it into a recoverable
    tool error or let the model retry it in the same session.
    """


@dataclass(frozen=True)
class AefSettings:
    """Configuration for the fixed test WorkStation profile."""

    base_url: str = TEST_WORKSTATION_URL
    environment: str = "test"
    ca_bundle: str | None = None
    create_secret: str | None = None
    idle_timeout_sec: int = 900
    absolute_ttl_sec: int = 3600
    heartbeat_sec: float = 60.0
    connect_timeout_sec: float = 10.0
    metadata_read_timeout_sec: float = 30.0
    safe_attempts: int = 4
    backoff_base_sec: float = 0.5
    backoff_cap_sec: float = 8.0
    cleanup_timeout_sec: float = 30.0
    attempt_deadline_guard_sec: int = 600
    max_download_workers: int = 4
    max_tool_output_chars: int = 400_000

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme.lower() != "https":
            raise AefConfigurationError("AEF_WORKSTATION_BASE_URL must use HTTPS; HTTP fallback is forbidden")
        if not parsed.hostname or parsed.username or parsed.password:
            raise AefConfigurationError("AEF_WORKSTATION_BASE_URL must be an HTTPS origin without credentials")
        if parsed.query or parsed.fragment:
            raise AefConfigurationError("AEF_WORKSTATION_BASE_URL must not contain a query or fragment")
        if parsed.path not in {"", "/"}:
            raise AefConfigurationError("AEF_WORKSTATION_BASE_URL must be an origin without a path")
        if self.ca_bundle and not Path(self.ca_bundle).is_file():
            raise AefConfigurationError(f"AEF_CA_BUNDLE is not a readable file: {self.ca_bundle}")
        if self.safe_attempts < 1 or self.max_download_workers < 1:
            raise AefConfigurationError("retry and download concurrency settings must be positive")
        if self.idle_timeout_sec <= 0 or self.absolute_ttl_sec <= 0:
            raise AefConfigurationError("session TTL values must be positive")
        if self.heartbeat_sec <= 0:
            raise AefConfigurationError("heartbeat interval must be positive")
        if self.backoff_base_sec < 0 or self.backoff_cap_sec < 0:
            raise AefConfigurationError("retry backoff values must be non-negative")
        if self.backoff_cap_sec and self.backoff_cap_sec < self.backoff_base_sec:
            raise AefConfigurationError("retry backoff cap must not be below its base")

    @property
    def httpx_verify(self) -> bool | str:
        return self.ca_bundle or True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AefSettings":
        values = os.environ if env is None else env

        def integer(name: str, default: int) -> int:
            raw = values.get(name)
            if raw is None or raw == "":
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise AefConfigurationError(f"{name} must be an integer") from exc

        try:
            heartbeat_sec = float(values.get("AEF_HEARTBEAT_SEC", "60"))
        except ValueError as exc:
            raise AefConfigurationError("AEF_HEARTBEAT_SEC must be numeric") from exc

        return cls(
            base_url=values.get("AEF_WORKSTATION_BASE_URL", TEST_WORKSTATION_URL).rstrip("/"),
            environment=values.get("AEF_WORKSTATION_ENV", "test"),
            ca_bundle=values.get("AEF_CA_BUNDLE") or None,
            create_secret=values.get("AEF_SESSION_CREATE_SECRET") or None,
            idle_timeout_sec=integer("AEF_IDLE_TIMEOUT_SEC", 900),
            absolute_ttl_sec=integer("AEF_ABSOLUTE_TTL_SEC", 3600),
            heartbeat_sec=heartbeat_sec,
        )


class MetadataLogger:
    """Allow-list-only structured event logger.

    Payloads, prompts, command text, output, authorization values, multipart
    bodies, and service ``workspaceRoot`` cannot enter emitted records because
    they are not accepted fields.
    """

    def __init__(
        self,
        sink: Callable[[dict[str, Any]], None] | None = None,
        *,
        run_id: str | None = None,
        attempt_no: int | None = None,
        attempt_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        self._sink = sink
        self._base = {
            "run_id": run_id,
            "attempt_no": attempt_no,
            "attempt_id": attempt_id,
            "thread_id": thread_id,
        }

    def emit(self, event: str, **fields: Any) -> None:
        if self._sink is None:
            return
        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
        }
        record.update({key: value for key, value in self._base.items() if value is not None})
        record.update({key: value for key, value in fields.items() if key in _SAFE_LOG_FIELDS and value is not None})
        self._sink(record)


class WriterPreferringRWLock:
    """A small writer-preferring readers-writer lock for one session."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._active_readers = 0
        self._writer_active = False
        self._waiting_writers = 0

    @contextmanager
    def read_lock(self) -> Iterator[None]:
        with self._condition:
            while self._writer_active or self._waiting_writers:
                self._condition.wait()
            self._active_readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._active_readers -= 1
                if self._active_readers == 0:
                    self._condition.notify_all()

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer_active or self._active_readers:
                    self._condition.wait()
                self._writer_active = True
            finally:
                self._waiting_writers -= 1
        try:
            yield
        finally:
            with self._condition:
                self._writer_active = False
                self._condition.notify_all()


@dataclass(frozen=True)
class AefSession:
    id: str
    token: str
    status: str
    created_at: datetime
    expires_at: datetime
    max_file_bytes: int
    max_total_bytes: int
    exec_timeout_sec: int


@dataclass(frozen=True)
class RemoteNode:
    name: str
    path: str
    kind: str
    size: int | None
    sha256: str | None
    children: tuple["RemoteNode", ...] = ()

    def walk(self, *, include_self: bool = True) -> Iterator["RemoteNode"]:
        if include_self:
            yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class RemoteFile:
    path: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class AefExecResult:
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    duration_ms: int | None = None


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise AefProtocolError(f"WorkStation response field {field_name} must be a date-time string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AefProtocolError(f"WorkStation response field {field_name} is not a valid date-time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _safe_log_path(path: str) -> str:
    if path == _WORKSPACE_ROOT:
        return "/"
    if path.startswith(_WORKSPACE_ROOT + "/"):
        return path[len(_WORKSPACE_ROOT) :]
    return path


def _service_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("code", "errorCode", "type"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) <= 128:
            return value
    error = payload.get("error")
    if isinstance(error, dict):
        value = error.get("code") or error.get("type")
        if isinstance(value, str) and len(value) <= 128:
            return value
    return None


class AefWorkstationClient:
    """Strict HTTPS client for the documented WorkStation API."""

    def __init__(
        self,
        settings: AefSettings,
        *,
        http_client: httpx.Client | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        run_id: str | None = None,
        attempt_no: int | None = None,
        thread_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=settings.base_url,
            verify=settings.httpx_verify,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                settings.metadata_read_timeout_sec,
                connect=settings.connect_timeout_sec,
            ),
        )
        self._logger = MetadataLogger(
            event_sink,
            run_id=run_id,
            attempt_no=attempt_no,
            attempt_id=f"{run_id}-attempt-{attempt_no}" if run_id and attempt_no is not None else None,
            thread_id=thread_id,
        )
        self._trace_id = run_id or uuid.uuid4().hex
        self._sleep = sleep
        self._random = random_source or random.Random()
        self._session: AefSession | None = None
        self._observed_expires_at: datetime | None = None
        self.orphan_session_ids: list[str] = []

    @property
    def session(self) -> AefSession:
        if self._session is None:
            raise AefInfrastructureError("WorkStation session has not been created")
        return self._session

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def _endpoint(self, path: str) -> str:
        if path.startswith("/v1/sessions/"):
            suffix = path[len("/v1/sessions/") :]
            if "/" in suffix:
                suffix = "{session_id}/" + suffix.split("/", 1)[1]
            elif suffix.endswith(":terminate"):
                suffix = "{session_id}:terminate"
            else:
                suffix = "{session_id}"
            return "/v1/sessions/" + suffix
        return path

    def _auth_headers(self, session: AefSession | None = None) -> dict[str, str]:
        active = session or self.session
        return {
            "Authorization": f"Bearer {active.token}",
            "X-Session-Id": active.id,
        }

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        request_id = uuid.uuid4().hex
        operation_id = uuid.uuid4().hex
        span_id = uuid.uuid4().hex
        all_headers = {
            "X-Request-Id": request_id,
            "X-Trace-Id": self._trace_id,
            "X-Span-Id": span_id,
        }
        if headers:
            all_headers.update(headers)
        session_id = self._session.id if self._session is not None else all_headers.get("X-Session-Id")
        ttl_remaining = (
            round((self._observed_expires_at - datetime.now(UTC)).total_seconds())
            if self._observed_expires_at is not None
            else None
        )
        started = time.monotonic()
        try:
            response = self._http.request(method, path, headers=all_headers, timeout=timeout, **kwargs)
        except httpx.TransportError as exc:
            self._logger.emit(
                "aef.http.failure",
                operation_id=operation_id,
                request_id=request_id,
                trace_id=self._trace_id,
                span_id=span_id,
                session_id=session_id,
                endpoint=self._endpoint(path),
                method=method,
                duration_ms=round((time.monotonic() - started) * 1000),
                ttl_remaining_sec=ttl_remaining,
                error_type=type(exc).__name__,
            )
            raise
        self._logger.emit(
            "aef.http.complete",
            operation_id=operation_id,
            request_id=request_id,
            trace_id=self._trace_id,
            span_id=span_id,
            session_id=session_id,
            endpoint=self._endpoint(path),
            method=method,
            http_status=response.status_code,
            service_error_code=_service_error_code(response) if response.is_error else None,
            duration_ms=round((time.monotonic() - started) * 1000),
            ttl_remaining_sec=ttl_remaining,
        )
        if 300 <= response.status_code < 400:
            raise AefConfigurationError(
                f"WorkStation returned redirect {response.status_code} for {self._endpoint(path)}; redirects are forbidden"
            )
        return response

    def _backoff(self, retry_index: int, response: httpx.Response | None = None) -> float:
        retry_after: float | None = None
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    retry_after = max(0.0, float(raw))
                except ValueError:
                    try:
                        when = parsedate_to_datetime(raw)
                        retry_after = max(0.0, (when - datetime.now(when.tzinfo or UTC)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        retry_after = None
        cap = min(
            self.settings.backoff_cap_sec,
            self.settings.backoff_base_sec * (2**retry_index),
        )
        if self.settings.backoff_cap_sec <= 0 or self.settings.backoff_base_sec <= 0:
            delay = 0.0
        elif retry_after is not None:
            delay = min(
                self.settings.backoff_cap_sec,
                max(self.settings.backoff_base_sec, retry_after),
            )
        else:
            delay = self._random.uniform(
                min(self.settings.backoff_base_sec, cap),
                cap,
            )
        self._logger.emit("aef.retry.backoff", retry=retry_index + 1, backoff_ms=round(delay * 1000))
        self._sleep(delay)
        return delay

    def _safe_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_transport: httpx.TransportError | None = None
        last_response: httpx.Response | None = None
        session_scoped = path.startswith("/v1/sessions/")
        ready_conflicts = 0
        for index in range(self.settings.safe_attempts):
            response: httpx.Response | None = None
            try:
                response = self._request_once(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_transport = exc
            else:
                last_response = response
                if response.status_code == 409 and session_scoped:
                    ready_conflicts += 1
                    status_path = f"/v1/sessions/{self.session.id}"
                    if path == status_path or ready_conflicts > 1:
                        raise AefAttemptLost(
                            "WorkStation session conflict persisted after one ready-state retry"
                        )
                    status = self.get_status()
                    if status.get("status") != "ready":
                        raise AefAttemptLost(
                            f"WorkStation session is not ready after conflict: {status.get('status')!r}"
                        )
                    if index + 1 < self.settings.safe_attempts:
                        continue
                    raise AefAttemptLost(
                        "WorkStation session conflict could not be retried within the configured policy"
                    )
                if response.status_code not in _SAFE_RETRY_STATUSES:
                    return response
            if index + 1 < self.settings.safe_attempts:
                self._backoff(index, response)
        if session_scoped:
            detail = (
                f"HTTP {last_response.status_code}"
                if last_response is not None
                else type(last_transport).__name__ if last_transport is not None else "unknown failure"
            )
            raise AefAttemptLost(
                f"WorkStation session operation remained unavailable after "
                f"{self.settings.safe_attempts} attempts ({detail})"
            ) from last_transport
        if last_transport is not None and last_response is None:
            raise AefInfrastructureError(
                f"WorkStation infrastructure request failed after {self.settings.safe_attempts} attempts"
            ) from last_transport
        assert last_response is not None
        return last_response

    @staticmethod
    def _require_json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise AefProtocolError("WorkStation returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise AefProtocolError("WorkStation JSON response must be an object")
        return value

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        code = _service_error_code(response)
        if response.status_code in _TOOL_ERROR_STATUSES:
            raise AefToolError(response.status_code, code)
        raise AefInfrastructureError(
            f"WorkStation {operation} failed with HTTP {response.status_code}"
            + (f" ({code})" if code else "")
        )

    def preflight(self) -> dict[str, Any]:
        """Verify HTTPS reachability, readiness, version, and API contract."""
        results: dict[str, Any] = {}
        for path in ("/health/readiness", "/version", "/openapi.json"):
            try:
                response = self._safe_request("GET", path)
            except AefInfrastructureError as exc:
                host = urlsplit(self.settings.base_url).hostname or self.settings.base_url
                raise AefInfrastructureError(
                    f"AEF WorkStation preflight failed for {host}; corporate DNS/VPN/IFT connectivity may be required"
                ) from exc
            self._raise_for_status(response, f"preflight {path}")
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                raise AefProtocolError(f"WorkStation {path} must return JSON, not {content_type or 'unknown content'}")
            payload = self._require_json_object(response)
            if path == "/health/readiness":
                status = payload.get("status")
                if not isinstance(status, str) or not status.strip():
                    raise AefProtocolError("WorkStation readiness response requires a status string")
                if status.strip().lower() not in {"ok", "ready", "up", "healthy"}:
                    raise AefInfrastructureError(
                        f"AEF WorkStation readiness endpoint reported {status!r}"
                    )
            else:
                if path == "/version":
                    version = payload.get("version")
                    if not isinstance(version, str) or not version.strip():
                        raise AefProtocolError("WorkStation version response requires a version string")
                else:
                    self._validate_openapi(payload)
            results[path] = payload
        return results

    @staticmethod
    def _validate_openapi(payload: Mapping[str, Any]) -> None:
        version = payload.get("openapi")
        paths = payload.get("paths")
        if not isinstance(version, str) or not version.strip() or not isinstance(paths, dict):
            raise AefProtocolError("WorkStation OpenAPI response is missing openapi/paths")

        normalized: dict[str, set[str]] = {}
        for raw_path, operations in paths.items():
            if not isinstance(raw_path, str) or not isinstance(operations, dict):
                continue
            canonical = re.sub(r"\{[^/{}]+\}", "{session_id}", raw_path)
            methods = normalized.setdefault(canonical, set())
            methods.update(
                str(method).lower()
                for method, operation in operations.items()
                if isinstance(method, str) and isinstance(operation, dict)
            )
        required = {
            "/v1/sessions": "post",
            "/v1/sessions/{session_id}": "get",
            "/v1/sessions/{session_id}:terminate": "post",
            "/v1/sessions/{session_id}/files:upload": "post",
            "/v1/sessions/{session_id}/files:download": "post",
            "/v1/sessions/{session_id}/files:list": "post",
            "/v1/sessions/{session_id}/exec": "post",
        }
        missing = [
            f"{method.upper()} {path}"
            for path, method in required.items()
            if method not in normalized.get(path, set())
        ]
        if missing:
            raise AefProtocolError(
                "WorkStation OpenAPI is missing required operations: " + ", ".join(missing)
            )

    def create_session(self) -> AefSession:
        """Create a new ready session; never reuse an ambiguous session ID."""
        body = {
            "idleTimeoutSec": self.settings.idle_timeout_sec,
            "absoluteTtlSec": self.settings.absolute_ttl_sec,
        }
        last_error: BaseException | None = None
        for index in range(self.settings.safe_attempts):
            session_id = f"agent-{uuid.uuid4().hex}"
            headers = {"X-Session-Id": session_id}
            if self.settings.create_secret:
                headers["X-Session-Create-Secret"] = self.settings.create_secret
            response: httpx.Response | None = None
            try:
                response = self._request_once("POST", "/v1/sessions", headers=headers, json=body)
            except httpx.TransportError as exc:
                last_error = exc
                self._mark_orphan(session_id, "ambiguous_transport")
            else:
                if response.status_code == 201:
                    payload: dict[str, Any] | None = None
                    try:
                        payload = self._require_json_object(response)
                        session = self._parse_created_session(
                            payload,
                            expected_id=session_id,
                        )
                    except AefProtocolError:
                        if payload is not None:
                            self._capture_provisional_session(
                                payload,
                                expected_id=session_id,
                            )
                        else:
                            self._mark_orphan(session_id, "malformed_create_without_json")
                        cleanup_ok = False
                        if self._session is not None:
                            try:
                                cleanup_ok = self.terminate()
                            except BaseException:
                                cleanup_ok = False
                        if not cleanup_ok:
                            self._mark_orphan(session_id, "malformed_create_cleanup_failed")
                        raise
                    self._session = session
                    self._observed_expires_at = session.expires_at
                    self._logger.emit(
                        "aef.session.created",
                        session_id=session.id,
                        status=session.status,
                        ttl_remaining_sec=round((session.expires_at - datetime.now(UTC)).total_seconds()),
                    )
                    return session
                if response.status_code == 200:
                    self._mark_orphan(session_id, "session_id_collision")
                    last_error = AefProtocolError("WorkStation reused a session ID generated for a new attempt")
                elif response.status_code == 409:
                    self._mark_orphan(session_id, "session_id_conflict")
                    last_error = AefInfrastructureError("WorkStation session ID conflicted")
                elif response.status_code in _CREATE_AMBIGUOUS_STATUSES:
                    self._mark_orphan(session_id, f"ambiguous_http_{response.status_code}")
                    last_error = AefInfrastructureError(
                        f"WorkStation session creation was ambiguous (HTTP {response.status_code})"
                    )
                else:
                    self._raise_for_status(response, "session creation")
            if index + 1 < self.settings.safe_attempts:
                self._backoff(index, response)
        raise AefInfrastructureError("Unable to create a fresh WorkStation session") from last_error

    def _mark_orphan(self, session_id: str, reason: str) -> None:
        if session_id not in self.orphan_session_ids:
            self.orphan_session_ids.append(session_id)
        self._logger.emit(
            "aef.session.orphan",
            session_id=session_id,
            orphan=True,
            cleanup_reason=reason,
        )

    def _capture_provisional_session(
        self,
        payload: Mapping[str, Any],
        *,
        expected_id: str,
    ) -> None:
        """Retain minimal credentials solely so a malformed 201 can be cleaned up."""

        token = payload.get("token")
        if payload.get("id") != expected_id or not isinstance(token, str) or not token:
            self._mark_orphan(expected_id, "malformed_create_without_cleanup_credentials")
            return
        now = datetime.now(UTC)
        self._session = AefSession(
            id=expected_id,
            token=token,
            status=str(payload.get("status") or "failed"),
            created_at=now,
            expires_at=now,
            max_file_bytes=1,
            max_total_bytes=1,
            exec_timeout_sec=1,
        )
        self._observed_expires_at = now

    @staticmethod
    def _parse_created_session(payload: Mapping[str, Any], *, expected_id: str) -> AefSession:
        session_id = payload.get("id")
        token = payload.get("token")
        status = payload.get("status")
        quotas = payload.get("quotas")
        if session_id != expected_id or not isinstance(token, str) or not token or status != "ready":
            raise AefProtocolError("New WorkStation session must be ready and return its matching ID and token")
        if not isinstance(quotas, dict):
            raise AefProtocolError("New WorkStation session did not return quotas")
        persistence = payload.get("workspacePersistence")
        if persistence is not None and (
            not isinstance(persistence, dict) or persistence.get("enabled") is not False
        ):
            raise AefProtocolError("WorkStation returned an invalid or enabled workspace persistence policy")
        try:
            max_file = quotas["maxFileBytes"]
            max_total = quotas["maxTotalBytes"]
            exec_timeout = quotas["execTimeoutSec"]
        except KeyError as exc:
            raise AefProtocolError("WorkStation session quotas are incomplete") from exc
        if any(type(value) is not int for value in (max_file, max_total, exec_timeout)):
            raise AefProtocolError("WorkStation session quotas must be integers")
        if min(max_file, max_total, exec_timeout) <= 0 or max_total < max_file:
            raise AefProtocolError("WorkStation session quotas must be positive")
        return AefSession(
            id=session_id,
            token=token,
            status=status,
            created_at=_parse_datetime(payload.get("createdAt"), "createdAt"),
            expires_at=_parse_datetime(payload.get("expiresAt"), "expiresAt"),
            max_file_bytes=max_file,
            max_total_bytes=max_total,
            exec_timeout_sec=exec_timeout,
        )

    def get_status(self, *, timeout: float | httpx.Timeout | None = None) -> dict[str, Any]:
        session = self.session
        try:
            response = self._safe_request(
                "GET",
                f"/v1/sessions/{session.id}",
                headers=self._auth_headers(session),
                timeout=timeout,
            )
            self._raise_for_status(response, "session status")
            payload = self._require_json_object(response)
            if payload.get("id") != session.id or not isinstance(payload.get("status"), str):
                raise AefProtocolError("WorkStation returned status for the wrong session")
            self._observed_expires_at = _parse_datetime(payload.get("expiresAt"), "expiresAt")
            return payload
        except AefAttemptLost:
            raise
        except AefError as exc:
            raise AefAttemptLost("WorkStation session status is invalid or unavailable") from exc

    @staticmethod
    def _parse_node(value: Any) -> RemoteNode:
        if not isinstance(value, dict):
            raise AefProtocolError("WorkStation file tree node must be an object")
        name = value.get("name")
        path = value.get("path")
        kind = value.get("kind")
        children = value.get("children", [])
        if not isinstance(name, str) or not isinstance(path, str) or kind not in {"file", "dir"}:
            raise AefProtocolError("WorkStation file tree node is malformed")
        if not isinstance(children, list):
            raise AefProtocolError("WorkStation file tree children must be an array")
        size_value = value.get("bytes")
        if size_value is not None and (type(size_value) is not int or size_value < 0):
            raise AefProtocolError("WorkStation file size must be a non-negative integer or null")
        size = size_value
        sha = value.get("sha256")
        if sha is not None and not _is_sha256(sha):
            raise AefProtocolError("WorkStation file SHA must be a SHA-256 string or null")
        if kind == "file" and children:
            raise AefProtocolError("WorkStation file node cannot contain children")
        return RemoteNode(
            name=name,
            path=path,
            kind=kind,
            size=size,
            sha256=sha.lower() if sha is not None else None,
            children=tuple(AefWorkstationClient._parse_node(child) for child in children),
        )

    def list_tree(
        self,
        path: str,
        *,
        recursive: bool = True,
        max_depth: int = 64,
        include_sha256: bool = True,
        missing_ok: bool = False,
    ) -> RemoteNode | None:
        session = self.session
        response = self._safe_request(
            "POST",
            f"/v1/sessions/{session.id}/files:list",
            headers=self._auth_headers(session),
            json={
                "path": path,
                "recursive": recursive,
                "maxDepth": max_depth,
                "includeSha256": include_sha256,
            },
        )
        if response.status_code == 404:
            if missing_ok:
                return None
            raise AefAttemptLost(f"Required WorkStation path disappeared: {path}")
        try:
            self._raise_for_status(response, "file list")
            return self._parse_node(self._require_json_object(response))
        except AefToolError:
            raise
        except AefAttemptLost:
            raise
        except AefError as exc:
            raise AefAttemptLost(f"WorkStation returned an invalid file listing for {path}") from exc

    def stat(self, path: str) -> RemoteNode | None:
        return self.list_tree(path, recursive=False, max_depth=0, include_sha256=True, missing_ok=True)

    def download(self, path: str) -> RemoteFile:
        session = self.session
        response = self._safe_request(
            "POST",
            f"/v1/sessions/{session.id}/files:download",
            headers=self._auth_headers(session),
            json={"path": path},
        )
        if response.status_code == 404:
            raise AefToolError(404, _service_error_code(response))
        try:
            self._raise_for_status(response, "file download")
            expected_sha = response.headers.get("X-File-Sha256")
            expected_bytes = response.headers.get("X-File-Bytes")
            actual_sha = _sha256(response.content)
            if not expected_sha or expected_sha.lower() != actual_sha:
                raise AefProtocolError("Downloaded file SHA-256 does not match WorkStation headers")
            try:
                byte_count = int(expected_bytes or "")
            except ValueError as exc:
                raise AefProtocolError("Downloaded file is missing a valid X-File-Bytes header") from exc
            if byte_count != len(response.content):
                raise AefProtocolError("Downloaded file size does not match WorkStation headers")
        except AefToolError:
            raise
        except AefAttemptLost:
            raise
        except AefError as exc:
            raise AefAttemptLost(f"Downloaded WorkStation file failed integrity checks: {path}") from exc
        self._logger.emit(
            "aef.file.download",
            session_id=session.id,
            virtual_path=_safe_log_path(path),
            bytes=len(response.content),
            sha256=actual_sha,
        )
        return RemoteFile(path=path, content=response.content, sha256=actual_sha)

    def upload(self, path: str, content: bytes) -> str:
        """Upload one file and reconcile ambiguous responses by remote SHA."""
        session = self.session
        expected_sha = _sha256(content)
        if len(content) > session.max_file_bytes:
            raise AefToolError(413, "local_max_file_bytes")
        ready_conflicts = 0
        for index in range(self.settings.safe_attempts):
            response: httpx.Response | None = None
            try:
                response = self._request_once(
                    "POST",
                    f"/v1/sessions/{session.id}/files:upload",
                    headers=self._auth_headers(session),
                    data={"path": path},
                    files={"file": (PurePosixPath(path).name or "upload.bin", content, "application/octet-stream")},
                )
            except httpx.TransportError:
                resolution = self._reconcile_upload(path, expected_sha)
                if resolution == "match":
                    return expected_sha
                if resolution == "foreign":
                    raise AefAttemptLost(f"Ambiguous upload produced an unexpected SHA for {path}")
            else:
                if response.status_code == 200:
                    acknowledgement_ok = False
                    try:
                        payload = self._require_json_object(response)
                        acknowledgement_ok = (
                            payload.get("path") == path
                            and payload.get("sha256") == expected_sha
                            and type(payload.get("bytes")) is int
                            and payload.get("bytes") == len(content)
                        )
                    except AefProtocolError:
                        acknowledgement_ok = False
                    if not acknowledgement_ok:
                        resolution = self._reconcile_upload(path, expected_sha)
                        if resolution != "match":
                            raise AefAttemptLost(
                                f"Upload acknowledgement could not be reconciled for {path}"
                            )
                    self._logger.emit(
                        "aef.file.upload",
                        session_id=session.id,
                        virtual_path=_safe_log_path(path),
                        bytes=len(content),
                        sha256=expected_sha,
                    )
                    return expected_sha
                if response.status_code == 409:
                    ready_conflicts += 1
                    if ready_conflicts > 1:
                        raise AefAttemptLost(
                            f"Upload conflict persisted after one ready-state retry for {path}"
                        )
                    status = self.get_status()
                    if status.get("status") != "ready":
                        raise AefAttemptLost("WorkStation session is not ready after upload conflict")
                    resolution = self._reconcile_upload(path, expected_sha)
                elif response.status_code in _SAFE_RETRY_STATUSES:
                    resolution = self._reconcile_upload(path, expected_sha)
                else:
                    try:
                        self._raise_for_status(response, "file upload")
                    except AefToolError:
                        raise
                    except AefError as exc:
                        raise AefAttemptLost(
                            f"WorkStation upload session became unavailable for {path}"
                        ) from exc
                    raise AssertionError("unreachable")
                if resolution == "match":
                    return expected_sha
                if resolution == "foreign":
                    raise AefAttemptLost(f"Upload reconciliation found an unexpected SHA for {path}")
            if index + 1 < self.settings.safe_attempts:
                self._backoff(index, response)
        raise AefAttemptLost(f"Unable to establish upload outcome for {path}; the attempt is tainted")

    def _reconcile_upload(self, path: str, expected_sha: str) -> str:
        try:
            node = self.stat(path)
        except AefAttemptLost:
            raise
        except AefError as exc:
            raise AefAttemptLost(f"Unable to reconcile upload state for {path}") from exc
        if node is None:
            return "absent"
        if node.kind != "file":
            return "foreign"
        sha = node.sha256
        if not sha:
            try:
                sha = self.download(path).sha256
            except AefAttemptLost:
                raise
            except AefError as exc:
                raise AefAttemptLost(f"Unable to reconcile upload SHA for {path}") from exc
        return "match" if sha == expected_sha else "foreign"

    def exec_sync(
        self,
        command: list[str],
        *,
        cwd: str = _WORKSPACE_ROOT,
        env: Mapping[str, str] | None = None,
        timeout_sec: int,
        retry_ready_conflict: bool = True,
        ambiguous_is_fatal: bool = True,
    ) -> AefExecResult:
        session = self.session
        payload = {
            "command": command,
            "cwd": cwd,
            "env": dict(env or {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}),
            "stdin": None,
            "stdinEncoding": "utf8",
            "timeoutSec": timeout_sec,
        }
        command_fingerprint = hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest()
        attempts = 2 if retry_ready_conflict else 1
        for index in range(attempts):
            try:
                response = self._request_once(
                    "POST",
                    f"/v1/sessions/{session.id}/exec",
                    headers=self._auth_headers(session),
                    json=payload,
                    timeout=httpx.Timeout(timeout_sec + 30.0, connect=self.settings.connect_timeout_sec),
                )
            except httpx.TransportError as exc:
                if ambiguous_is_fatal:
                    self._logger.emit(
                        "aef.attempt.tainted",
                        session_id=session.id,
                        tainted=True,
                        restart_reason="ambiguous_exec_transport",
                        command_hash=command_fingerprint,
                        command_length=sum(len(part) for part in command),
                        timeout_sec=timeout_sec,
                    )
                    raise AefAttemptLost("Arbitrary execute response was lost; outcome is ambiguous") from exc
                raise
            if response.status_code == 200:
                try:
                    result = self._parse_exec_result(self._require_json_object(response))
                except AefProtocolError as exc:
                    if ambiguous_is_fatal:
                        self._logger.emit(
                            "aef.attempt.tainted",
                            session_id=session.id,
                            tainted=True,
                            restart_reason="ambiguous_exec_malformed_success",
                            command_hash=command_fingerprint,
                            command_length=sum(len(part) for part in command),
                            timeout_sec=timeout_sec,
                        )
                        raise AefAttemptLost(
                            "Arbitrary execute completed but returned an invalid response"
                        ) from exc
                    raise
                self._logger.emit(
                    "aef.exec.complete",
                    session_id=session.id,
                    command_hash=command_fingerprint,
                    command_length=sum(len(part) for part in command),
                    timeout_sec=timeout_sec,
                    exit_code=result.exit_code,
                    truncated=result.stdout_truncated or result.stderr_truncated,
                    duration_ms=result.duration_ms,
                )
                return result
            if response.status_code == 504:
                return AefExecResult(
                    exit_code=124,
                    stdout="",
                    stderr="Command timed out",
                    stdout_truncated=False,
                    stderr_truncated=False,
                    timed_out=True,
                )
            if response.status_code == 409:
                if index == 0 and retry_ready_conflict:
                    status = self.get_status()
                    if status.get("status") == "ready":
                        continue
                    error = "WorkStation session is not ready after execute conflict"
                else:
                    error = "WorkStation execute conflict persisted after ready-state retry"
                if ambiguous_is_fatal:
                    raise AefAttemptLost(error)
                raise AefInfrastructureError(error)
            if response.status_code == 429:
                raise AefToolError(429, _service_error_code(response))
            if response.status_code in _TOOL_ERROR_STATUSES:
                raise AefToolError(response.status_code, _service_error_code(response))
            if response.status_code == 408 or 500 <= response.status_code < 600:
                if ambiguous_is_fatal:
                    self._logger.emit(
                        "aef.attempt.tainted",
                        session_id=session.id,
                        tainted=True,
                        restart_reason=f"ambiguous_exec_http_{response.status_code}",
                        command_hash=command_fingerprint,
                        command_length=sum(len(part) for part in command),
                        timeout_sec=timeout_sec,
                    )
                    raise AefAttemptLost(
                        f"Arbitrary execute returned ambiguous HTTP {response.status_code}"
                    )
            try:
                self._raise_for_status(response, "execute")
            except AefToolError:
                raise
            except AefError as exc:
                if ambiguous_is_fatal:
                    raise AefAttemptLost(
                        f"WorkStation execute session failed with HTTP {response.status_code}"
                    ) from exc
                raise
        error = "WorkStation execute conflict persisted after ready-state retry"
        if ambiguous_is_fatal:
            raise AefAttemptLost(error)
        raise AefInfrastructureError(error)

    @staticmethod
    def _parse_exec_result(payload: Mapping[str, Any]) -> AefExecResult:
        try:
            exit_code = payload["exitCode"]
        except KeyError as exc:
            raise AefProtocolError("WorkStation exec response is missing exitCode") from exc
        if type(exit_code) is not int:
            raise AefProtocolError("WorkStation exec exitCode must be an integer")
        stdout = payload.get("stdout")
        stderr = payload.get("stderr")
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise AefProtocolError("WorkStation exec stdout/stderr must be strings")
        flag_names = ("stdoutTruncated", "stderrTruncated", "timedOut")
        if any(type(payload.get(name)) is not bool for name in flag_names):
            raise AefProtocolError("WorkStation exec timeout and truncation fields must be booleans")
        timed_out = payload["timedOut"]
        if timed_out:
            exit_code = 124
        duration = payload.get("durationMs")
        if duration is not None and (type(duration) is not int or duration < 0):
            raise AefProtocolError("WorkStation exec durationMs must be a non-negative integer or null")
        return AefExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=payload["stdoutTruncated"],
            stderr_truncated=payload["stderrTruncated"],
            timed_out=timed_out,
            duration_ms=duration,
        )

    def ensure_directory(self, path: str) -> None:
        node = self.stat(path)
        if node is not None:
            if node.kind != "dir":
                raise AefProtocolError(f"Expected directory but found file: {path}")
            return
        for attempt in range(2):
            outcome_was_ambiguous = False
            try:
                result = self.exec_sync(
                    ["mkdir", "-p", path],
                    timeout_sec=min(30, self.session.exec_timeout_sec),
                    retry_ready_conflict=True,
                    ambiguous_is_fatal=False,
                )
                if result.exit_code != 0:
                    raise AefInfrastructureError(f"WorkStation could not create directory {path}")
            except (httpx.TransportError, AefInfrastructureError, AefProtocolError):
                outcome_was_ambiguous = True
            try:
                node = self.stat(path)
            except AefError as exc:
                if outcome_was_ambiguous:
                    raise AefAttemptLost(
                        f"Unable to reconcile ambiguous directory creation for {path}"
                    ) from exc
                raise
            if node is not None and node.kind == "dir":
                return
            if attempt == 0:
                continue
        raise AefAttemptLost(f"Unable to establish directory creation outcome for {path}")

    def terminate(self, *, deadline_monotonic: float | None = None) -> bool:
        if self._session is None:
            return True
        deadline = deadline_monotonic or (time.monotonic() + self.settings.cleanup_timeout_sec)
        session = self._session
        last_status: int | None = None
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                response = self._request_once(
                    "POST",
                    f"/v1/sessions/{session.id}:terminate",
                    headers=self._auth_headers(session),
                    timeout=min(self.settings.metadata_read_timeout_sec, remaining),
                )
            except httpx.TransportError:
                self._sleep(min(0.5, max(0.0, deadline - time.monotonic())))
                continue
            last_status = response.status_code
            if response.status_code in {200, 404}:
                self._logger.emit(
                    "aef.session.cleanup",
                    session_id=session.id,
                    cleanup_status="complete",
                    http_status=response.status_code,
                )
                return True
            if response.status_code == 409:
                try:
                    self._request_once(
                        "GET",
                        f"/v1/sessions/{session.id}",
                        headers=self._auth_headers(session),
                        timeout=min(self.settings.metadata_read_timeout_sec, remaining),
                    )
                except (httpx.TransportError, AefError):
                    pass
                self._sleep(min(0.5, max(0.0, deadline - time.monotonic())))
                continue
            break
        self._logger.emit(
            "aef.session.cleanup",
            session_id=session.id,
            cleanup_status="failed",
            http_status=last_status,
            cleanup_reason="cleanup_deadline_or_error",
        )
        return False


def _virtual_path(path: str) -> str:
    if "\x00" in path:
        raise ValueError("NUL is not allowed in virtual paths")
    return validate_path(path)


def _physical_path(path: str) -> str:
    virtual = _virtual_path(path)
    return _WORKSPACE_ROOT if virtual == "/" else _WORKSPACE_ROOT + virtual


def _from_physical_path(path: str) -> str:
    if path == _WORKSPACE_ROOT:
        return "/"
    if not path.startswith(_WORKSPACE_ROOT + "/"):
        raise AefProtocolError("WorkStation returned a path outside /workspace")
    return _virtual_path(path[len(_WORKSPACE_ROOT) :])


def _is_protected(path: str) -> bool:
    normalized = _virtual_path(path)
    return any(normalized == root or normalized.startswith(root + "/") for root in _PROTECTED_ROOTS)


def _validate_shallow_listing(expected_path: str, listed: RemoteNode) -> str:
    try:
        canonical = _physical_path(_from_physical_path(listed.path))
        if canonical != expected_path:
            raise AefProtocolError(
                f"WorkStation listed {listed.path!r} while {expected_path!r} was requested"
            )
        if listed.name != PurePosixPath(canonical).name:
            raise AefProtocolError(f"WorkStation returned an inconsistent root name for {canonical}")
        if listed.kind == "file" and listed.children:
            raise AefProtocolError(f"WorkStation returned children for file {canonical}")
        seen_children: set[str] = set()
        for child in listed.children:
            child_path = _physical_path(_from_physical_path(child.path))
            if PurePosixPath(child_path).parent != PurePosixPath(canonical):
                raise AefProtocolError(
                    f"WorkStation returned a non-direct child {child.path!r} for {canonical!r}"
                )
            if child.name != PurePosixPath(child_path).name:
                raise AefProtocolError(f"WorkStation returned an inconsistent node name for {child_path}")
            if child.children:
                raise AefProtocolError(
                    f"WorkStation returned recursive children for a non-recursive list of {canonical}"
                )
            if child_path in seen_children:
                raise AefProtocolError(f"WorkStation returned duplicate node {child_path}")
            seen_children.add(child_path)
        return canonical
    except AefAttemptLost:
        raise
    except (AefProtocolError, ValueError) as exc:
        raise AefAttemptLost(
            f"WorkStation returned an invalid shallow listing for {expected_path}"
        ) from exc


def _walk_remote_tree(
    client: AefWorkstationClient,
    physical_root: str,
    *,
    missing_ok: bool,
    max_nodes: int = 100_000,
) -> list[RemoteNode]:
    """Walk a WorkStation tree without trusting a server-side depth limit.

    Every request is deliberately non-recursive.  Besides removing the former
    depth-64 blind spot, this validates that the service never returns a node
    outside the requested subtree or skips an intermediate directory.
    """

    root = _physical_path(_from_physical_path(physical_root))
    pending = deque([root])
    nodes: dict[str, RemoteNode] = {}
    while pending:
        current = pending.popleft()
        listed = client.list_tree(
            current,
            recursive=False,
            max_depth=1,
            include_sha256=True,
            missing_ok=missing_ok and current == root,
        )
        if listed is None:
            return []
        canonical = _validate_shallow_listing(current, listed)
        existing = nodes.get(canonical)
        if existing is not None and existing.kind != listed.kind:
            raise AefProtocolError(f"WorkStation changed node kind while listing {canonical}")
        nodes[canonical] = listed
        if listed.kind == "file":
            if listed.children:
                raise AefProtocolError(f"WorkStation returned children for file {canonical}")
            continue
        for child in listed.children:
            child_path = _physical_path(_from_physical_path(child.path))
            if child_path in nodes:
                raise AefProtocolError(f"WorkStation returned duplicate node {child_path}")
            nodes[child_path] = child
            if len(nodes) > max_nodes:
                raise AefProtocolError(
                    f"WorkStation tree exceeds the safety limit of {max_nodes} nodes"
                )
            if child.kind == "dir":
                pending.append(child_path)
    return list(nodes.values())


def _glob_pattern(pattern: str) -> str:
    if "\x00" in pattern:
        raise ValueError("NUL is not allowed in glob patterns")
    if "\\" in pattern:
        raise ValueError("Backslashes are not allowed in POSIX glob patterns")
    effective = pattern.lstrip("/")
    parts = PurePosixPath(effective).parts
    if ".." in parts:
        raise ValueError("Path traversal not allowed in glob pattern")
    if parts and len(parts[0]) >= 2 and parts[0][1] == ":":
        raise ValueError("Windows drive paths are not allowed in glob patterns")
    return effective


def _recursive_glob_match(relative_path: str, pattern: str) -> bool:
    if not pattern:
        return False
    recursive_pattern = pattern if pattern.startswith("**/") else f"**/{pattern}"
    return wcglob.globmatch(
        relative_path,
        recursive_pattern,
        flags=wcglob.BRACE | wcglob.GLOBSTAR | wcglob.DOTMATCH,
    )


class AefWorkstationBackend(SandboxBackendProtocol):
    """Deep Agents sandbox backend backed solely by one WorkStation session."""

    def __init__(
        self,
        client: AefWorkstationClient,
        *,
        lock: WriterPreferringRWLock | None = None,
        integrity_check: Callable[[], None] | None = None,
        attempt_check: Callable[[], float] | None = None,
    ) -> None:
        self.client = client
        self._lock = lock or WriterPreferringRWLock()
        self._integrity_check = integrity_check
        self._attempt_check = attempt_check
        self._cache: dict[tuple[str, str], bytes] = {}
        self._cache_index: dict[str, str] = {}
        self._cache_lock = threading.Lock()

    def _check_attempt(self) -> float:
        if self._attempt_check is None:
            return float("inf")
        return self._attempt_check()

    @property
    def id(self) -> str:
        return self.client.session.id

    @staticmethod
    def to_physical_path(path: str) -> str:
        return _physical_path(path)

    @staticmethod
    def to_virtual_path(path: str) -> str:
        return _from_physical_path(path)

    def _node_info(self, node: RemoteNode) -> FileInfo:
        virtual = _from_physical_path(node.path)
        if node.kind == "dir" and virtual != "/":
            virtual += "/"
        info: FileInfo = {"path": virtual, "is_dir": node.kind == "dir"}
        if node.size is not None:
            info["size"] = node.size
        elif node.kind == "dir":
            info["size"] = 0
        return info

    def _download_bytes(self, virtual_path: str, *, known_sha: str | None = None) -> bytes:
        physical = _physical_path(virtual_path)
        sha = known_sha
        if sha is None:
            node = self.client.stat(physical)
            if node is None or node.kind != "file":
                raise FileNotFoundError(virtual_path)
            sha = node.sha256
        if sha:
            with self._cache_lock:
                cached = self._cache.get((virtual_path, sha))
            if cached is not None:
                return cached
        remote = self.client.download(physical)
        with self._cache_lock:
            self._cache[(virtual_path, remote.sha256)] = remote.content
            self._cache_index[virtual_path] = remote.sha256
        return remote.content

    def _invalidate(self, virtual_path: str | None = None) -> None:
        with self._cache_lock:
            if virtual_path is None:
                self._cache.clear()
                self._cache_index.clear()
                return
            sha = self._cache_index.pop(virtual_path, None)
            if sha:
                self._cache.pop((virtual_path, sha), None)

    def ls(self, path: str) -> LsResult:
        try:
            self._check_attempt()
            virtual = _virtual_path(path)
            with self._lock.read_lock():
                node = self.client.list_tree(
                    _physical_path(virtual), recursive=False, max_depth=1, include_sha256=True, missing_ok=True
                )
            self._check_attempt()
            if node is None or node.kind != "dir":
                return LsResult(entries=[])
            _validate_shallow_listing(_physical_path(virtual), node)
            return LsResult(entries=sorted(
                (self._node_info(child) for child in node.children),
                key=lambda entry: entry["path"],
            ))
        except (ValueError, AefPolicyError, AefToolError) as exc:
            return LsResult(error=str(exc))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            self._check_attempt()
            virtual = _virtual_path(file_path)
            with self._lock.read_lock():
                content = self._download_bytes(virtual)
            self._check_attempt()
            if _get_file_type(virtual) != "text":
                encoded = base64.standard_b64encode(content).decode("ascii")
                return ReadResult(file_data=create_file_data(encoded, encoding="base64"))
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                encoded = base64.standard_b64encode(content).decode("ascii")
                return ReadResult(file_data=create_file_data(encoded, encoding="base64"))
            sliced = slice_read_response(create_file_data(text), offset, limit)
            if isinstance(sliced, ReadResult):
                return sliced
            return ReadResult(file_data=create_file_data(sliced))
        except FileNotFoundError:
            return ReadResult(error=f"File not found: {file_path}")
        except ValueError as exc:
            return ReadResult(error=str(exc))
        except AefToolError as exc:
            return ReadResult(error=str(exc))

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            self._check_attempt()
            virtual = _virtual_path(file_path)
            if _is_protected(virtual):
                return WriteResult(error=f"Permission denied: {virtual}")
            physical = _physical_path(virtual)
            payload = content.encode("utf-8")
            with self._lock.write_lock():
                if self.client.stat(physical) is not None:
                    return WriteResult(error=f"File already exists: {virtual}")
                self.client.ensure_directory(str(PurePosixPath(physical).parent))
                actual_sha = self.client.upload(physical, payload)
                if actual_sha != _sha256(payload):
                    raise AefAttemptLost(f"Write SHA verification failed for {virtual}")
                self._invalidate(virtual)
                self._check_attempt()
            return WriteResult(path=virtual)
        except (ValueError, AefToolError, AefPolicyError) as exc:
            return WriteResult(error=str(exc))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            self._check_attempt()
            virtual = _virtual_path(file_path)
            if _is_protected(virtual):
                return EditResult(error=f"Permission denied: {virtual}")
            with self._lock.write_lock():
                try:
                    original = self._download_bytes(virtual).decode("utf-8")
                except FileNotFoundError:
                    return EditResult(error=f"File not found: {virtual}")
                except UnicodeDecodeError:
                    return EditResult(error=f"File is not valid UTF-8 text: {virtual}")
                # Match FilesystemBackend's universal-newline semantics.  The
                # read tool exposes CRLF/CR files as LF, so edit must compare
                # against the exact same representation and persist LF bytes.
                original = original.replace("\r\n", "\n").replace("\r", "\n")
                old_string = old_string.replace("\r\n", "\n").replace("\r", "\n")
                new_string = new_string.replace("\r\n", "\n").replace("\r", "\n")
                replacement = perform_string_replacement(original, old_string, new_string, replace_all)
                if isinstance(replacement, str):
                    return EditResult(error=replacement)
                changed, occurrences = replacement
                payload = changed.encode("utf-8")
                actual_sha = self.client.upload(_physical_path(virtual), payload)
                if actual_sha != _sha256(payload):
                    raise AefAttemptLost(f"Edit SHA verification failed for {virtual}")
                self._invalidate(virtual)
                self._check_attempt()
            return EditResult(path=virtual, occurrences=occurrences)
        except (ValueError, AefToolError, AefPolicyError) as exc:
            return EditResult(error=str(exc))

    def _recursive_nodes(self, path: str = "/") -> list[RemoteNode]:
        return _walk_remote_tree(
            self.client,
            _physical_path(path),
            missing_ok=True,
        )

    def _recursive_files(self, path: str = "/", *, directory_only: bool = False) -> list[RemoteNode]:
        nodes = self._recursive_nodes(path)
        if not nodes or (directory_only and nodes[0].kind != "dir"):
            return []
        return [node for node in nodes if node.kind == "file"]

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        effective = _glob_pattern(pattern)
        try:
            self._check_attempt()
            base = _virtual_path(path)
            with self._lock.read_lock():
                files = self._recursive_files(base, directory_only=True)
            matches: list[FileInfo] = []
            for node in files:
                virtual = _from_physical_path(node.path)
                relative = PurePosixPath(virtual).name if virtual == base else virtual[len(base.rstrip("/")) + 1 :]
                if base == "/":
                    relative = virtual.lstrip("/")
                if _recursive_glob_match(relative, effective):
                    matches.append(self._node_info(node))
            matches.sort(key=lambda entry: entry["path"])
            self._check_attempt()
            return GlobResult(matches=matches)
        except (ValueError, AefToolError) as exc:
            return GlobResult(error=str(exc))

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        effective_glob = _glob_pattern(glob) if glob else None
        try:
            self._check_attempt()
            base = _virtual_path(path or "/")
            with self._lock.read_lock():
                nodes = self._recursive_nodes(base)
                exact_file = bool(nodes and nodes[0].kind == "file")
                files = [node for node in nodes if node.kind == "file"]
                file_data: dict[str, Any] = {}
                for node in files:
                    virtual = _from_physical_path(node.path)
                    if base == "/":
                        relative = virtual.lstrip("/")
                    elif virtual == base:
                        relative = PurePosixPath(virtual).name
                    else:
                        relative = virtual[len(base.rstrip("/")) + 1 :]
                    if effective_glob and not exact_file and not _recursive_glob_match(relative, effective_glob):
                        continue
                    try:
                        text = self._download_bytes(virtual, known_sha=node.sha256).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    file_data[virtual] = create_file_data(text)
            result = grep_matches_from_files(file_data, pattern, base, None)
            self._check_attempt()
            return result
        except (ValueError, AefToolError) as exc:
            return GrepResult(error=str(exc))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self._check_attempt()
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                virtual = _virtual_path(path)
                if _is_protected(virtual):
                    responses.append(FileUploadResponse(path=path, error="permission_denied"))
                    continue
                with self._lock.write_lock():
                    physical = _physical_path(virtual)
                    self.client.ensure_directory(str(PurePosixPath(physical).parent))
                    actual = self.client.upload(physical, content)
                    if actual != _sha256(content):
                        raise AefAttemptLost(f"Upload SHA verification failed for {virtual}")
                    self._invalidate(virtual)
                    self._check_attempt()
                responses.append(FileUploadResponse(path=virtual))
            except ValueError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
            except AefToolError as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        self._check_attempt()
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self._check_attempt()
        def one(path: str) -> FileDownloadResponse:
            try:
                virtual = _virtual_path(path)
                content = self._download_bytes(virtual)
                return FileDownloadResponse(path=virtual, content=content)
            except ValueError:
                return FileDownloadResponse(path=path, error="invalid_path")
            except FileNotFoundError:
                return FileDownloadResponse(path=path, error="file_not_found")
            except AefToolError as exc:
                if exc.status_code == 404:
                    return FileDownloadResponse(path=path, error="file_not_found")
                return FileDownloadResponse(path=path, error=str(exc))

        with self._lock.read_lock():
            with ThreadPoolExecutor(max_workers=min(self.client.settings.max_download_workers, max(1, len(paths)))) as pool:
                responses = list(pool.map(one, paths))
        self._check_attempt()
        return responses

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        remaining_attempt = self._check_attempt()
        quota = self.client.session.exec_timeout_sec
        requested_timeout = quota if timeout in {None, 0} else timeout
        if not isinstance(requested_timeout, int) or requested_timeout < 0:
            return ExecuteResponse(output="Error: timeout must be a non-negative integer", exit_code=2)
        if requested_timeout > quota:
            return ExecuteResponse(
                output=f"Error: timeout {requested_timeout}s exceeds WorkStation quota {quota}s",
                exit_code=2,
            )
        if requested_timeout + 30 > remaining_attempt:
            raise AefAttemptLost(
                "WorkStation execute cannot complete before the safe attempt deadline"
            )
        wrapper = (
            "export PYTHONPATH=/workspace/.harness_runtime "
            "DEEPAGENT_WORKSPACE_ROOT=/workspace PYTHONUTF8=1 PYTHONIOENCODING=utf-8; "
            + command
        )
        try:
            with self._lock.write_lock():
                try:
                    result = self.client.exec_sync(
                        ["sh", "-lc", wrapper],
                        cwd=_WORKSPACE_ROOT,
                        env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                        timeout_sec=requested_timeout,
                    )
                finally:
                    self._invalidate()
                if self._integrity_check is not None:
                    self._integrity_check()
                self._check_attempt()
        except AefToolError as exc:
            return ExecuteResponse(output=f"Error: {exc}", exit_code=2)
        output = result.stdout
        if result.stderr:
            output = output + (("\n" if output and not output.endswith("\n") else "") + result.stderr)
        client_truncated = len(output) > self.client.settings.max_tool_output_chars
        if client_truncated:
            output = output[: self.client.settings.max_tool_output_chars]
        return ExecuteResponse(
            output=output,
            exit_code=124 if result.timed_out else result.exit_code,
            truncated=result.stdout_truncated or result.stderr_truncated or client_truncated,
        )


@dataclass(frozen=True)
class StagingEntry:
    virtual_path: str
    local_path: str
    size: int
    sha256: str


@dataclass
class AttemptReport:
    attempt_no: int
    thread_id: str
    attempt_id: str | None = None
    session_id: str | None = None
    status: str = "starting"
    restart_reason: str | None = None
    cleanup_status: str | None = None
    cleanup_duration_ms: int | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ClientFactory = Callable[..., AefWorkstationClient]


class AefSessionManager:
    """Own one clean WorkStation session, its heartbeat, and source integrity."""

    def __init__(
        self,
        settings: AefSettings,
        *,
        client_factory: ClientFactory | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        run_id: str | None = None,
        attempt_no: int = 1,
        thread_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.run_id = run_id or uuid.uuid4().hex
        self.attempt_no = attempt_no
        self.thread_id = thread_id or f"{self.run_id}-attempt-{attempt_no}-{uuid.uuid4().hex[:12]}"
        factory = client_factory or AefWorkstationClient
        self.client = factory(
            settings,
            event_sink=event_sink,
            run_id=self.run_id,
            attempt_no=attempt_no,
            thread_id=self.thread_id,
        )
        self._logger = MetadataLogger(
            event_sink,
            run_id=self.run_id,
            attempt_no=attempt_no,
            attempt_id=f"{self.run_id}-attempt-{attempt_no}",
            thread_id=self.thread_id,
        )
        self._backend: AefWorkstationBackend | None = None
        self._manifest: tuple[StagingEntry, ...] = ()
        self._protected_fingerprint: dict[str, str] = {}
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_error: BaseException | None = None
        self._attempt_deadline_monotonic: float | None = None
        self._closed = False

    @property
    def backend(self) -> AefWorkstationBackend:
        if self._backend is None:
            raise AefInfrastructureError("WorkStation session has not been staged")
        return self._backend

    @property
    def session(self) -> AefSession:
        return self.client.session

    @property
    def manifest(self) -> tuple[StagingEntry, ...]:
        return self._manifest

    def _build_manifest(self, staging_root: Path) -> tuple[StagingEntry, ...]:
        root = staging_root.resolve()
        if not root.is_dir():
            raise AefConfigurationError(f"Staging root is not a directory: {root}")
        candidates: dict[str, Path] = {}
        for local in sorted(path for path in root.rglob("*") if path.is_file()):
            relative = local.relative_to(root).as_posix()
            if not relative.startswith(("inputs/", "skills/", ".harness_runtime/", "harness_runtime/")):
                continue
            if relative.startswith("harness_runtime/"):
                virtual = "/.harness_runtime/" + relative.removeprefix("harness_runtime/")
            else:
                virtual = "/" + relative
            candidates[_virtual_path(virtual)] = local

        runtime_source = Path(__file__).resolve().parent / "harness_runtime" / "sitecustomize.py"
        runtime_virtual = "/.harness_runtime/sitecustomize.py"
        if runtime_virtual not in candidates and runtime_source.is_file():
            candidates[runtime_virtual] = runtime_source

        required = {
            "/inputs/contract.txt",
            "/inputs/matrix.json",
            "/skills/contract-matrix-review/SKILL.md",
            runtime_virtual,
        }
        missing = sorted(required.difference(candidates))
        if missing:
            raise AefConfigurationError("Staging root is missing required files: " + ", ".join(missing))

        entries: list[StagingEntry] = []
        for virtual, local in sorted(candidates.items()):
            content = local.read_bytes()
            entries.append(
                StagingEntry(
                    virtual_path=virtual,
                    local_path=str(local),
                    size=len(content),
                    sha256=_sha256(content),
                )
            )
        return tuple(entries)

    def start(
        self,
        staging_root: str | Path,
        *,
        expected_manifest: Mapping[str, tuple[int, str]] | None = None,
    ) -> AefWorkstationBackend:
        self._manifest = self._build_manifest(Path(staging_root))
        if expected_manifest is not None:
            actual_manifest = {
                entry.virtual_path: (entry.size, entry.sha256)
                for entry in self._manifest
            }
            if actual_manifest != dict(expected_manifest):
                raise AefConfigurationError(
                    "Local staging files changed after the immutable run snapshot"
                )
        self.client.preflight()
        session = self.client.create_session()
        ttl_remaining = (session.expires_at - datetime.now(UTC)).total_seconds()
        granted_ttl = (session.expires_at - session.created_at).total_seconds()
        if granted_ttl < self.settings.absolute_ttl_sec:
            raise AefConfigurationError(
                "WorkStation granted an absolute TTL shorter than the requested test profile"
            )
        if ttl_remaining <= self.settings.attempt_deadline_guard_sec:
            raise AefConfigurationError(
                "WorkStation absolute TTL is too short for the configured attempt deadline guard"
            )
        self._set_attempt_deadline(session.expires_at)
        total_bytes = sum(item.size for item in self._manifest)
        if total_bytes > session.max_total_bytes:
            raise AefConfigurationError("Staging manifest exceeds WorkStation total file quota")
        oversized = [item.virtual_path for item in self._manifest if item.size > session.max_file_bytes]
        if oversized:
            raise AefConfigurationError("Staging file exceeds WorkStation file quota: " + oversized[0])

        directories = {
            "/inputs",
            "/outputs",
            "/outputs/working",
            "/skills",
            "/tmp",
            "/large_tool_results",
            "/conversation_history",
            "/.harness_runtime",
        }
        directories.update(str(PurePosixPath(item.virtual_path).parent) for item in self._manifest)
        for directory in sorted(directories, key=lambda item: (item.count("/"), item)):
            self.client.ensure_directory(_physical_path(directory))

        for item in self._manifest:
            content = Path(item.local_path).read_bytes()
            if _sha256(content) != item.sha256 or len(content) != item.size:
                raise AefConfigurationError(f"Staging source changed while being uploaded: {item.virtual_path}")
            actual = self.client.upload(_physical_path(item.virtual_path), content)
            if actual != item.sha256:
                raise AefAttemptLost(f"Staging SHA mismatch for {item.virtual_path}")

        protected_fingerprint: dict[str, str] = {
            root: "dir" for root in _PROTECTED_ROOTS
        }
        for item in self._manifest:
            if not _is_protected(item.virtual_path):
                continue
            protected_fingerprint[item.virtual_path] = f"file:{item.sha256}"
            for parent in PurePosixPath(item.virtual_path).parents:
                parent_path = str(parent)
                if parent_path == ".":
                    parent_path = "/"
                if _is_protected(parent_path):
                    protected_fingerprint[parent_path] = "dir"
        self._protected_fingerprint = protected_fingerprint
        lock = WriterPreferringRWLock()
        self._backend = AefWorkstationBackend(
            self.client,
            lock=lock,
            integrity_check=self._verify_integrity_unlocked,
            attempt_check=self.check_attempt_active,
        )
        self.verify_integrity()
        self._start_heartbeat()
        self._logger.emit(
            "aef.session.ready",
            session_id=session.id,
            status="ready",
            ttl_remaining_sec=round(ttl_remaining),
        )
        return self._backend

    def _set_attempt_deadline(self, expires_at: datetime) -> None:
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        candidate = time.monotonic() + remaining - self.settings.attempt_deadline_guard_sec
        if self._attempt_deadline_monotonic is None:
            self._attempt_deadline_monotonic = candidate
        else:
            # A status response may shorten an expiry but may never silently
            # extend the validity window of the current attempt.
            self._attempt_deadline_monotonic = min(self._attempt_deadline_monotonic, candidate)

    def check_attempt_active(self) -> float:
        """Fail closed once heartbeat or the safe absolute deadline taints the attempt."""

        if self._heartbeat_error is not None:
            if isinstance(self._heartbeat_error, AefAttemptLost):
                raise self._heartbeat_error
            raise AefAttemptLost("WorkStation heartbeat failed") from self._heartbeat_error
        if self._attempt_deadline_monotonic is None:
            raise AefAttemptLost("WorkStation attempt deadline is not initialized")
        remaining = self._attempt_deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise AefAttemptLost("WorkStation reached expiresAt minus the safety guard")
        return remaining

    def _start_heartbeat(self) -> None:
        def run() -> None:
            while not self._heartbeat_stop.wait(self.settings.heartbeat_sec):
                try:
                    status = self.client.get_status(
                        timeout=min(5.0, self.settings.metadata_read_timeout_sec)
                    )
                    if status.get("status") != "ready":
                        raise AefAttemptLost(
                            f"WorkStation heartbeat observed session status {status.get('status')!r}"
                        )
                    expires_at = _parse_datetime(status.get("expiresAt"), "expiresAt")
                    self._set_attempt_deadline(expires_at)
                    if (expires_at - datetime.now(UTC)).total_seconds() <= self.settings.attempt_deadline_guard_sec:
                        raise AefAttemptLost("WorkStation heartbeat reached the safe attempt deadline")
                except BaseException as exc:  # stored for the orchestrator thread
                    self._heartbeat_error = exc if isinstance(exc, AefAttemptLost) else AefAttemptLost(
                        "WorkStation heartbeat could no longer establish session health"
                    )
                    self._logger.emit(
                        "aef.heartbeat.failure",
                        session_id=self.client.session.id,
                        error_type=type(exc).__name__,
                    )
                    return

        self._heartbeat_thread = threading.Thread(
            target=run,
            name=f"aef-heartbeat-{self.client.session.id}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def ensure_healthy(self) -> dict[str, Any]:
        self.check_attempt_active()
        try:
            status = self.client.get_status()
        except AefError as exc:
            raise AefAttemptLost("WorkStation session health could not be established") from exc
        if status.get("status") != "ready":
            raise AefAttemptLost(f"WorkStation session is not ready: {status.get('status')!r}")
        expires_at = _parse_datetime(status.get("expiresAt"), "expiresAt")
        self._set_attempt_deadline(expires_at)
        remaining = (expires_at - datetime.now(UTC)).total_seconds()
        if remaining <= self.settings.attempt_deadline_guard_sec:
            raise AefAttemptLost("WorkStation session no longer has enough TTL for a safe attempt")
        self.check_attempt_active()
        return status

    def _current_protected_fingerprint(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for protected_root in _PROTECTED_ROOTS:
            nodes = _walk_remote_tree(
                self.client,
                _physical_path(protected_root),
                missing_ok=False,
            )
            for node in nodes:
                virtual = _from_physical_path(node.path)
                if not _is_protected(virtual):
                    raise AefProtocolError(
                        f"Protected-tree scan escaped its root at {virtual}"
                    )
                if node.kind == "dir":
                    result[virtual] = "dir"
                else:
                    sha = node.sha256 or self.client.download(node.path).sha256
                    result[virtual] = f"file:{sha.lower()}"
        return result

    def _verify_integrity_unlocked(self) -> None:
        try:
            current = self._current_protected_fingerprint()
        except AefAttemptLost:
            raise
        except AefError as exc:
            self._logger.emit(
                "aef.attempt.tainted",
                session_id=self.client.session.id,
                tainted=True,
                restart_reason="protected_source_integrity_unverifiable",
                error_type=type(exc).__name__,
            )
            raise AefAttemptLost(
                "Protected input, skill, and runtime integrity could not be established"
            ) from exc
        if current != self._protected_fingerprint:
            self._logger.emit(
                "aef.attempt.tainted",
                session_id=self.client.session.id,
                tainted=True,
                restart_reason="protected_source_integrity_mismatch",
            )
            raise AefAttemptLost("Protected inputs, skills, or runtime changed inside WorkStation")

    def verify_integrity(self) -> None:
        self._verify_integrity_unlocked()

    def download_result(self, path: str = "/outputs/result.json") -> bytes:
        response = self.backend.download_files([path])[0]
        if response.error or response.content is None:
            raise AefInfrastructureError(f"Unable to download {path}: {response.error or 'empty response'}")
        return response.content

    def terminate(self) -> bool:
        if self._closed:
            return True
        self._closed = True
        cleanup_deadline = time.monotonic() + self.settings.cleanup_timeout_sec
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(
                timeout=max(0.0, cleanup_deadline - time.monotonic())
            )
            if self._heartbeat_thread.is_alive():
                # Never close an httpx client while the heartbeat still owns
                # an in-flight request. Report the bounded cleanup failure and
                # defer only the local client close until that request exits;
                # the remote session is left for its TTL cleanup.
                self._logger.emit(
                    "aef.session.cleanup",
                    session_id=self._safe_client_session_id(),
                    cleanup_status="failed",
                    cleanup_reason="heartbeat_shutdown_timeout",
                    orphan=True,
                )
                heartbeat = self._heartbeat_thread
                client = self.client

                def close_after_heartbeat() -> None:
                    heartbeat.join()
                    try:
                        client.close()
                    except BaseException:
                        pass

                threading.Thread(
                    target=close_after_heartbeat,
                    name=f"aef-deferred-close-{self._safe_client_session_id() or 'unknown'}",
                    daemon=True,
                ).start()
                return False
        cleanup_ok = False
        try:
            if time.monotonic() < cleanup_deadline:
                cleanup_ok = self.client.terminate(
                    deadline_monotonic=cleanup_deadline
                )
        except BaseException as exc:
            self._logger.emit(
                "aef.session.cleanup",
                session_id=self._safe_client_session_id(),
                cleanup_status="failed",
                cleanup_reason="terminate_exception",
                error_type=type(exc).__name__,
            )
        try:
            self.client.close()
        except BaseException as exc:
            cleanup_ok = False
            self._logger.emit(
                "aef.session.cleanup",
                session_id=self._safe_client_session_id(),
                cleanup_status="failed",
                cleanup_reason="client_close_exception",
                error_type=type(exc).__name__,
            )
        return cleanup_ok

    def _safe_client_session_id(self) -> str | None:
        try:
            return self.client.session.id
        except AefInfrastructureError:
            return None

    close = terminate

    def __enter__(self) -> "AefSessionManager":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.terminate()


T = TypeVar("T")


class RunSupervisor(Generic[T]):
    """Restart a tainted attempt once, always with fresh attempt state."""

    def __init__(
        self,
        settings: AefSettings,
        *,
        run_id: str | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        max_attempts: int = 2,
        manager_factory: Callable[..., AefSessionManager] | None = None,
        expected_manifest: Mapping[str, tuple[int, str]] | None = None,
    ) -> None:
        if max_attempts < 1 or max_attempts > 2:
            raise AefConfigurationError("RunSupervisor supports one initial attempt and at most one restart")
        self.settings = settings
        self.run_id = run_id or uuid.uuid4().hex
        self.event_sink = event_sink
        self.max_attempts = max_attempts
        self.manager_factory = manager_factory or AefSessionManager
        self.expected_manifest = (
            dict(expected_manifest) if expected_manifest is not None else None
        )
        self.reports: list[dict[str, Any]] = []
        self._logger = MetadataLogger(event_sink, run_id=self.run_id)

    def run(
        self,
        staging_root: str | Path,
        attempt_callback: Callable[[AefSessionManager, AefWorkstationBackend, str, int], T],
    ) -> T:
        last_lost: AefAttemptLost | None = None
        for attempt_no in range(1, self.max_attempts + 1):
            attempt_started = time.monotonic()
            attempt_id = f"{self.run_id}-attempt-{attempt_no}"
            thread_id = f"{self.run_id}-attempt-{attempt_no}-{uuid.uuid4().hex[:12]}"
            report = AttemptReport(
                attempt_no=attempt_no,
                attempt_id=attempt_id,
                thread_id=thread_id,
            )
            manager = self.manager_factory(
                self.settings,
                event_sink=self.event_sink,
                run_id=self.run_id,
                attempt_no=attempt_no,
                thread_id=thread_id,
            )
            try:
                if self.expected_manifest is None:
                    backend = manager.start(staging_root)
                else:
                    backend = manager.start(
                        staging_root,
                        expected_manifest=self.expected_manifest,
                    )
                report.session_id = backend.id
                report.status = "running"
                value = attempt_callback(manager, backend, thread_id, attempt_no)
                manager.ensure_healthy()
                manager.verify_integrity()
                report.status = "complete"
                return value
            except AefAttemptLost as exc:
                last_lost = exc
                report.session_id = self._safe_session_id(manager)
                report.status = "restarting" if attempt_no < self.max_attempts else "failed"
                report.restart_reason = str(exc)
                self._logger.emit(
                    "aef.attempt.restart",
                    attempt_no=attempt_no,
                    thread_id=thread_id,
                    session_id=report.session_id,
                    status=report.status,
                    restart_reason=type(exc).__name__,
                )
                if attempt_no >= self.max_attempts:
                    raise
            except Exception as exc:
                report.session_id = self._safe_session_id(manager)
                report.status = "failed"
                report.restart_reason = type(exc).__name__
                raise
            finally:
                cleanup_started = time.monotonic()
                try:
                    cleanup_ok = manager.terminate()
                except BaseException as cleanup_exc:
                    cleanup_ok = False
                    self._logger.emit(
                        "aef.session.cleanup",
                        attempt_no=attempt_no,
                        thread_id=thread_id,
                        session_id=report.session_id,
                        cleanup_status="failed",
                        cleanup_reason="supervisor_cleanup_exception",
                        error_type=type(cleanup_exc).__name__,
                    )
                report.cleanup_duration_ms = round((time.monotonic() - cleanup_started) * 1000)
                report.cleanup_status = "complete" if cleanup_ok else "failed"
                report.duration_ms = round((time.monotonic() - attempt_started) * 1000)
                self.reports.append(report.to_dict())
        assert last_lost is not None
        raise last_lost

    @staticmethod
    def _safe_session_id(manager: AefSessionManager) -> str | None:
        try:
            return manager.client.session.id
        except AefInfrastructureError:
            return None


__all__ = [
    "AefAttemptLost",
    "AefConfigurationError",
    "AefError",
    "AefExecResult",
    "AefInfrastructureError",
    "AefPolicyError",
    "AefProtocolError",
    "AefSession",
    "AefSessionManager",
    "AefSettings",
    "AefToolError",
    "AefWorkstationBackend",
    "AefWorkstationClient",
    "AttemptReport",
    "MetadataLogger",
    "RemoteFile",
    "RemoteNode",
    "RunSupervisor",
    "StagingEntry",
    "TEST_WORKSTATION_URL",
    "WriterPreferringRWLock",
]
