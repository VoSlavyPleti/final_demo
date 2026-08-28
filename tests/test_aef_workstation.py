from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from deepagents.middleware.skills import _list_skills_with_errors

from aef_workstation import (
    AefAttemptLost,
    AefConfigurationError,
    AefExecResult,
    AefInfrastructureError,
    AefProtocolError,
    AefSession,
    AefSessionManager,
    AefSettings,
    AefToolError,
    AefWorkstationBackend,
    AefWorkstationClient,
    MetadataLogger,
    RemoteFile,
    RemoteNode,
    RunSupervisor,
    TEST_WORKSTATION_URL,
    WriterPreferringRWLock,
)


FUTURE = "2099-01-01T00:00:00Z"
CREATED = "2026-01-01T00:00:00Z"


def _openapi_payload(*, omit: str | None = None) -> dict[str, Any]:
    paths = {
        "/v1/sessions": {"post": {}},
        "/v1/sessions/{id}": {"get": {}},
        "/v1/sessions/{id}:terminate": {"post": {}},
        "/v1/sessions/{id}/files:upload": {"post": {}},
        "/v1/sessions/{id}/files:download": {"post": {}},
        "/v1/sessions/{id}/files:list": {"post": {}},
        "/v1/sessions/{id}/exec": {"post": {}},
    }
    if omit:
        paths.pop(omit)
    return {"openapi": "3.1.0", "paths": paths}


def _session(session_id: str = "session-1") -> AefSession:
    return AefSession(
        id=session_id,
        token="opaque-token",
        status="ready",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        max_file_bytes=20 * 1024 * 1024,
        max_total_bytes=200 * 1024 * 1024,
        exec_timeout_sec=300,
    )


def _created_payload(session_id: str) -> dict[str, Any]:
    return {
        "id": session_id,
        "status": "ready",
        "token": "opaque-token",
        "createdAt": CREATED,
        "expiresAt": FUTURE,
        "quotas": {
            "maxFileBytes": 20 * 1024 * 1024,
            "maxTotalBytes": 200 * 1024 * 1024,
            "execTimeoutSec": 300,
        },
    }


class MemoryClient:
    """Minimal in-memory WorkStation client used to test backend semantics."""

    def __init__(self, settings: AefSettings | None = None, **_: Any) -> None:
        self.settings = settings or AefSettings(heartbeat_sec=999)
        self.session = _session()
        self.files: dict[str, bytes] = {}
        self.directories = {"/workspace"}
        self.upload_log: list[str] = []
        self.download_log: list[str] = []
        self.exec_log: list[tuple[list[str], dict[str, Any]]] = []
        self.terminated = False

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def ensure_directory(self, path: str) -> None:
        current = PurePosixPath(path)
        while str(current).startswith("/workspace"):
            self.directories.add(str(current))
            if str(current) == "/workspace":
                break
            current = current.parent

    def upload(self, path: str, content: bytes) -> str:
        self.ensure_directory(str(PurePosixPath(path).parent))
        self.files[path] = content
        self.upload_log.append(path)
        return self._sha(content)

    def download(self, path: str) -> RemoteFile:
        if path not in self.files:
            from aef_workstation import AefToolError

            raise AefToolError(404)
        content = self.files[path]
        self.download_log.append(path)
        return RemoteFile(path=path, content=content, sha256=self._sha(content))

    def _node(self, path: str, recursive: bool) -> RemoteNode | None:
        if path in self.files:
            content = self.files[path]
            return RemoteNode(
                name=PurePosixPath(path).name,
                path=path,
                kind="file",
                size=len(content),
                sha256=self._sha(content),
            )
        if path not in self.directories and not any(name.startswith(path.rstrip("/") + "/") for name in self.files):
            return None
        self.directories.add(path)
        child_paths: set[str] = set()
        prefix = path.rstrip("/") + "/"
        for directory in self.directories:
            if directory.startswith(prefix):
                suffix = directory[len(prefix) :]
                if suffix and (recursive or "/" not in suffix):
                    child_paths.add(directory if recursive else prefix + suffix.split("/", 1)[0])
        for filename in self.files:
            if filename.startswith(prefix):
                suffix = filename[len(prefix) :]
                if recursive or "/" not in suffix:
                    child_paths.add(filename if recursive else prefix + suffix.split("/", 1)[0])
        immediate = sorted(
            candidate
            for candidate in child_paths
            if recursive or "/" not in candidate[len(prefix) :]
        )
        if recursive:
            immediate = sorted(
                candidate
                for candidate in child_paths
                if "/" not in candidate[len(prefix) :]
            )
        children = tuple(node for child in immediate if (node := self._node(child, recursive)) is not None)
        return RemoteNode(
            name=PurePosixPath(path).name,
            path=path,
            kind="dir",
            size=None,
            sha256=None,
            children=children,
        )

    def stat(self, path: str) -> RemoteNode | None:
        node = self._node(path, False)
        if node is None:
            return None
        return RemoteNode(node.name, node.path, node.kind, node.size, node.sha256)

    def list_tree(
        self,
        path: str,
        *,
        recursive: bool = True,
        max_depth: int = 64,
        include_sha256: bool = True,
        missing_ok: bool = False,
    ) -> RemoteNode | None:
        del max_depth, include_sha256, missing_ok
        node = self._node(path, recursive)
        if node is None or recursive or node.kind == "file":
            return node
        children = tuple(
            RemoteNode(child.name, child.path, child.kind, child.size, child.sha256)
            for child in node.children
        )
        return RemoteNode(node.name, node.path, node.kind, node.size, node.sha256, children)

    def exec_sync(self, command: list[str], **kwargs: Any) -> AefExecResult:
        self.exec_log.append((command, kwargs))
        if command[:2] == ["mkdir", "-p"]:
            self.ensure_directory(command[2])
        return AefExecResult(
            exit_code=0,
            stdout="ok\n",
            stderr="warning\n",
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            duration_ms=2,
        )


class LifecycleClient(MemoryClient):
    def preflight(self) -> dict[str, Any]:
        return {
            "/health/readiness": {"status": "ok"},
            "/version": {"version": "test"},
            "/openapi.json": _openapi_payload(),
        }

    def create_session(self) -> AefSession:
        return self.session

    def get_status(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"id": self.session.id, "status": "ready", "expiresAt": FUTURE}

    def terminate(self, **kwargs: Any) -> bool:
        del kwargs
        self.terminated = True
        return True

    def close(self) -> None:
        pass


def _mock_client(handler: Any, settings: AefSettings | None = None) -> AefWorkstationClient:
    configured = settings or AefSettings(
        safe_attempts=2,
        backoff_base_sec=0,
        backoff_cap_sec=0,
        heartbeat_sec=999,
    )
    http = httpx.Client(
        base_url=configured.base_url,
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    return AefWorkstationClient(configured, http_client=http, sleep=lambda _: None)


def test_settings_are_https_only_and_logger_is_payload_safe(tmp_path: Path) -> None:
    assert AefSettings.from_env({}).base_url == TEST_WORKSTATION_URL
    with pytest.raises(AefConfigurationError, match="HTTPS"):
        AefSettings(base_url="http://workstation.example")
    with pytest.raises(AefConfigurationError, match="without a path"):
        AefSettings(base_url="https://workstation.example/api")

    ca = tmp_path / "corporate-ca.pem"
    ca.write_text("test", encoding="utf-8")
    settings = AefSettings.from_env(
        {
            "AEF_WORKSTATION_BASE_URL": "https://workstation.example",
            "AEF_WORKSTATION_ENV": "test",
            "AEF_CA_BUNDLE": str(ca),
            "AEF_SESSION_CREATE_SECRET": "do-not-log",
        }
    )
    assert settings.httpx_verify == str(ca)

    events: list[dict[str, Any]] = []
    MetadataLogger(events.append, run_id="run").emit(
        "operation",
        session_id="session",
        command="cat confidential.txt",
        stdout="secret document",
        token="secret-token",
        prompt="secret prompt",
        bytes=12,
    )
    encoded = json.dumps(events, ensure_ascii=False)
    assert events[0]["bytes"] == 12
    assert all(word not in encoded for word in ("confidential", "document", "secret-token", "secret prompt"))


def test_create_uses_new_id_after_ambiguous_response_and_never_sends_persistence() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("lost create response", request=request)
        session_id = request.headers["X-Session-Id"]
        return httpx.Response(201, json=_created_payload(session_id), request=request)

    settings = AefSettings(
        create_secret="create-secret",
        safe_attempts=2,
        backoff_base_sec=0,
        backoff_cap_sec=0,
    )
    client = _mock_client(handler, settings)
    session = client.create_session()

    first_id = requests[0].headers["X-Session-Id"]
    second_id = requests[1].headers["X-Session-Id"]
    assert first_id != second_id == session.id
    assert client.orphan_session_ids == [first_id]
    assert requests[1].headers["X-Session-Create-Secret"] == "create-secret"
    assert json.loads(requests[1].content) == {"idleTimeoutSec": 900, "absoluteTtlSec": 3600}


def test_create_rejects_server_side_workspace_persistence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = _created_payload(request.headers["X-Session-Id"])
        payload["workspacePersistence"] = {"enabled": True, "root": "/workspace", "scopeId": "unexpected"}
        return httpx.Response(201, json=payload, request=request)

    client = _mock_client(handler)
    with pytest.raises(AefProtocolError, match="persistence"):
        client.create_session()
    assert len(client.orphan_session_ids) == 1


def test_malformed_create_is_terminated_when_cleanup_credentials_exist() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(":terminate"):
            assert request.headers["Authorization"] == "Bearer opaque-token"
            return httpx.Response(200, json={"status": "terminated"}, request=request)
        payload = _created_payload(request.headers["X-Session-Id"])
        payload.pop("quotas")
        return httpx.Response(201, json=payload, request=request)

    client = _mock_client(handler)
    with pytest.raises(AefProtocolError, match="quotas"):
        client.create_session()
    assert [request.url.path for request in seen][-1].endswith(":terminate")


def test_session_scoped_safe_request_exhaustion_loses_attempt() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"code": "unavailable"}, request=request)

    client = _mock_client(handler)
    client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="remained unavailable"):
        client.get_status()
    assert calls == client.settings.safe_attempts


def test_malformed_session_status_list_and_download_lose_attempt() -> None:
    def status_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "other-session", "status": "ready", "expiresAt": FUTURE},
            request=request,
        )

    status_client = _mock_client(status_handler)
    status_client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="status is invalid"):
        status_client.get_status()

    def list_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"kind": "dir"}, request=request)

    list_client = _mock_client(list_handler)
    list_client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="invalid file listing"):
        list_client.list_tree("/workspace")

    def download_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"payload",
            headers={"X-File-Sha256": "0" * 64, "X-File-Bytes": "7"},
            request=request,
        )

    download_client = _mock_client(download_handler)
    download_client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="integrity checks"):
        download_client.download("/workspace/out.txt")


def test_preflight_retries_safe_endpoint_and_checks_api_contract() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/health/readiness" and seen.count("/health/readiness") == 1:
            return httpx.Response(503, json={"code": "not_ready"}, request=request)
        if request.url.path == "/health/readiness":
            return httpx.Response(200, json={"status": "ready"}, request=request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1"}, request=request)
        return httpx.Response(200, json=_openapi_payload(), request=request)

    client = _mock_client(handler)
    result = client.preflight()
    assert seen == [
        "/health/readiness",
        "/health/readiness",
        "/version",
        "/openapi.json",
    ]
    assert result["/version"] == {"version": "1"}
    assert result["/openapi.json"]["openapi"] == "3.1.0"


def test_preflight_rejects_explicit_not_ready_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/readiness":
            return httpx.Response(200, json={"status": "not_ready"}, request=request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1"}, request=request)
        return httpx.Response(200, json=_openapi_payload(), request=request)

    with pytest.raises(AefInfrastructureError, match="not_ready"):
        _mock_client(handler).preflight()


@pytest.mark.parametrize(
    ("readiness", "version", "message"),
    [
        ({}, {"version": "1"}, "status string"),
        ({"status": "ready"}, {}, "version string"),
    ],
)
def test_preflight_requires_documented_json_fields(
    readiness: dict[str, Any], version: dict[str, Any], message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/readiness":
            payload = readiness
        elif request.url.path == "/version":
            payload = version
        else:
            payload = _openapi_payload()
        return httpx.Response(200, json=payload, request=request)

    with pytest.raises(AefProtocolError, match=message):
        _mock_client(handler).preflight()


def test_preflight_rejects_html_login_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>login</html>",
            headers={"Content-Type": "text/html"},
            request=request,
        )

    with pytest.raises(AefProtocolError, match="must return JSON"):
        _mock_client(handler).preflight()


def test_preflight_rejects_openapi_without_required_operation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/readiness":
            return httpx.Response(200, json={"status": "ready"}, request=request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "1"}, request=request)
        return httpx.Response(
            200,
            json=_openapi_payload(omit="/v1/sessions/{id}/exec"),
            request=request,
        )

    with pytest.raises(AefProtocolError, match="POST /v1/sessions/.*/exec"):
        _mock_client(handler).preflight()


def test_backoff_honors_bounds_and_clamps_retry_after() -> None:
    delays: list[float] = []

    class MidpointRandom:
        def uniform(self, start: float, end: float) -> float:
            return (start + end) / 2

    settings = AefSettings(safe_attempts=2, backoff_base_sec=0.5, backoff_cap_sec=8)
    http = httpx.Client(
        base_url=settings.base_url,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)),
    )
    client = AefWorkstationClient(
        settings,
        http_client=http,
        sleep=delays.append,
        random_source=MidpointRandom(),  # type: ignore[arg-type]
    )
    assert client._backoff(1) == 0.75  # noqa: SLF001
    response = httpx.Response(
        429,
        headers={"Retry-After": "120"},
        request=httpx.Request("GET", settings.base_url),
    )
    assert client._backoff(5, response) == 8  # noqa: SLF001
    assert delays == [0.75, 8]


def test_execute_conflict_retries_once_but_ambiguous_failure_never_retries() -> None:
    calls: list[tuple[str, str]] = []

    def conflict_handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer opaque-token"
        assert request.headers["X-Session-Id"] == "session-1"
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "session-1", "status": "ready", "expiresAt": FUTURE},
                request=request,
            )
        if calls.count(("POST", "/v1/sessions/session-1/exec")) == 1:
            return httpx.Response(409, json={"code": "busy"}, request=request)
        payload = json.loads(request.content)
        assert payload["command"] == ["sh", "-lc", "echo ok"]
        assert payload["env"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        return httpx.Response(
            200,
            json={
                "exitCode": 0,
                "stdout": "ok\n",
                "stderr": "",
                "stdoutTruncated": False,
                "stderrTruncated": False,
                "durationMs": 3,
                "timedOut": False,
                "workspaceRoot": "/host/path/must-be-ignored",
            },
            request=request,
        )

    client = _mock_client(conflict_handler)
    client._session = _session()  # noqa: SLF001 - controlled transport unit test
    result = client.exec_sync(["sh", "-lc", "echo ok"], timeout_sec=30)
    assert result.stdout == "ok\n"
    assert calls == [
        ("POST", "/v1/sessions/session-1/exec"),
        ("GET", "/v1/sessions/session-1"),
        ("POST", "/v1/sessions/session-1/exec"),
    ]

    ambiguous_calls = 0

    def ambiguous_handler(request: httpx.Request) -> httpx.Response:
        nonlocal ambiguous_calls
        ambiguous_calls += 1
        return httpx.Response(503, json={"code": "runtime_unknown"}, request=request)

    ambiguous = _mock_client(ambiguous_handler)
    ambiguous._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="ambiguous HTTP 503"):
        ambiguous.exec_sync(["sh", "-lc", "touch outputs/x"], timeout_sec=30)
    assert ambiguous_calls == 1


def test_persistent_execute_conflict_and_malformed_success_lose_attempt() -> None:
    conflict_calls: list[tuple[str, str]] = []

    def conflict_handler(request: httpx.Request) -> httpx.Response:
        conflict_calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "session-1", "status": "ready", "expiresAt": FUTURE},
                request=request,
            )
        return httpx.Response(409, json={"code": "busy"}, request=request)

    conflict = _mock_client(conflict_handler)
    conflict._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="persisted"):
        conflict.exec_sync(["sh", "-lc", "touch outputs/x"], timeout_sec=30)
    assert conflict_calls == [
        ("POST", "/v1/sessions/session-1/exec"),
        ("GET", "/v1/sessions/session-1"),
        ("POST", "/v1/sessions/session-1/exec"),
    ]

    malformed_calls = 0

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        nonlocal malformed_calls
        malformed_calls += 1
        return httpx.Response(200, json={"stdout": "", "stderr": ""}, request=request)

    malformed = _mock_client(malformed_handler)
    malformed._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="invalid response"):
        malformed.exec_sync(["sh", "-lc", "touch outputs/x"], timeout_sec=30)
    assert malformed_calls == 1


def test_file_list_409_checks_ready_and_retries_once() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "session-1", "status": "ready", "expiresAt": FUTURE},
                request=request,
            )
        if calls.count(("POST", "/v1/sessions/session-1/files:list")) == 1:
            return httpx.Response(409, json={"code": "busy"}, request=request)
        return httpx.Response(
            200,
            json={
                "name": "workspace",
                "path": "/workspace",
                "kind": "dir",
                "bytes": None,
                "sha256": None,
                "children": [],
            },
            request=request,
        )

    client = _mock_client(handler)
    client._session = _session()  # noqa: SLF001
    assert client.list_tree("/workspace") is not None
    assert calls == [
        ("POST", "/v1/sessions/session-1/files:list"),
        ("GET", "/v1/sessions/session-1"),
        ("POST", "/v1/sessions/session-1/files:list"),
    ]


def test_upload_download_and_list_follow_documented_wire_schema() -> None:
    content = "данные".encode()
    digest = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files:upload"):
            body = request.content
            assert b'name="path"' in body
            assert b"/workspace/data.txt" in body
            assert b'name="file"' in body
            assert content in body
            return httpx.Response(
                200,
                json={"path": "/workspace/data.txt", "bytes": len(content), "sha256": digest},
                request=request,
            )
        if request.url.path.endswith("/files:download"):
            assert json.loads(request.content) == {"path": "/workspace/data.txt"}
            return httpx.Response(
                200,
                content=content,
                headers={"X-File-Sha256": digest, "X-File-Bytes": str(len(content))},
                request=request,
            )
        assert request.url.path.endswith("/files:list")
        assert json.loads(request.content) == {
            "path": "/workspace",
            "recursive": True,
            "maxDepth": 5,
            "includeSha256": True,
        }
        return httpx.Response(
            200,
            json={
                "name": "workspace",
                "path": "/workspace",
                "kind": "dir",
                "bytes": None,
                "sha256": None,
                "children": [
                    {
                        "name": "data.txt",
                        "path": "/workspace/data.txt",
                        "kind": "file",
                        "bytes": len(content),
                        "sha256": digest,
                        "children": [],
                    }
                ],
            },
            request=request,
        )

    client = _mock_client(handler)
    client._session = _session()  # noqa: SLF001
    assert client.upload("/workspace/data.txt", content) == digest
    assert client.download("/workspace/data.txt").content == content
    tree = client.list_tree("/workspace", max_depth=5)
    assert tree is not None and tree.children[0].sha256 == digest


def test_execute_504_and_service_timed_out_are_exit_124() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, json={"code": "command_timeout"}, request=request)

    client = _mock_client(timeout_handler)
    client._session = _session()  # noqa: SLF001
    result = client.exec_sync(["sh", "-lc", "sleep 999"], timeout_sec=30)
    assert result.timed_out is True
    assert result.exit_code == 124

    def throttled_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": "rate_limited"}, request=request)

    throttled = _mock_client(throttled_handler)
    throttled._session = _session()  # noqa: SLF001
    with pytest.raises(AefToolError) as error:
        throttled.exec_sync(["sh", "-lc", "true"], timeout_sec=30)
    assert error.value.status_code == 429


@pytest.mark.parametrize("status", [400, 403, 413, 422])
def test_execute_explicit_request_rejections_are_tool_errors(status: int) -> None:
    client = _mock_client(
        lambda request: httpx.Response(
            status,
            json={"code": f"rejected_{status}"},
            request=request,
        )
    )
    client._session = _session()  # noqa: SLF001

    with pytest.raises(AefToolError) as error:
        client.exec_sync(["sh", "-lc", "true"], timeout_sec=30)
    assert error.value.status_code == status


def test_execute_auth_failure_taints_attempt_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            401,
            json={"code": "invalid_session_token"},
            request=request,
        )

    client = _mock_client(handler)
    client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="execute session failed"):
        client.exec_sync(["sh", "-lc", "true"], timeout_sec=30)
    assert calls == 1


@pytest.mark.parametrize("resolution,raises", [("match", False), ("foreign", True)])
def test_upload_reconciles_lost_response_by_sha(monkeypatch: pytest.MonkeyPatch, resolution: str, raises: bool) -> None:
    client = _mock_client(lambda request: httpx.Response(500, request=request))
    client._session = _session()  # noqa: SLF001
    request = httpx.Request("POST", TEST_WORKSTATION_URL + "/upload")

    def lost(*args: Any, **kwargs: Any) -> httpx.Response:
        del args, kwargs
        raise httpx.ReadTimeout("lost", request=request)

    monkeypatch.setattr(client, "_request_once", lost)
    monkeypatch.setattr(client, "_reconcile_upload", lambda path, sha: resolution)
    if raises:
        with pytest.raises(AefAttemptLost):
            client.upload("/workspace/out.txt", b"payload")
    else:
        assert client.upload("/workspace/out.txt", b"payload") == hashlib.sha256(b"payload").hexdigest()


def test_upload_409_gets_only_one_ready_state_retry() -> None:
    upload_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_calls
        if request.url.path.endswith("/files:upload"):
            upload_calls += 1
            return httpx.Response(409, json={"code": "busy"}, request=request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "session-1", "status": "ready", "expiresAt": FUTURE},
                request=request,
            )
        return httpx.Response(404, json={"code": "missing"}, request=request)

    client = _mock_client(
        handler,
        AefSettings(
            safe_attempts=4,
            backoff_base_sec=0,
            backoff_cap_sec=0,
            heartbeat_sec=999,
        ),
    )
    client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="one ready-state retry"):
        client.upload("/workspace/out.txt", b"payload")
    assert upload_calls == 2


def test_malformed_upload_acknowledgement_requires_remote_sha_match() -> None:
    foreign = hashlib.sha256(b"foreign").hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/files:upload"):
            return httpx.Response(200, json={"unexpected": True}, request=request)
        return httpx.Response(
            200,
            json={
                "name": "out.txt",
                "path": "/workspace/out.txt",
                "kind": "file",
                "bytes": 7,
                "sha256": foreign,
                "children": [],
            },
            request=request,
        )

    client = _mock_client(handler)
    client._session = _session()  # noqa: SLF001
    with pytest.raises(AefAttemptLost, match="acknowledgement"):
        client.upload("/workspace/out.txt", b"payload")


def test_backend_implements_vfs_semantics_protection_search_cache_and_execute() -> None:
    client = MemoryClient()
    client.upload("/workspace/inputs/contract.txt", b"protected")
    client.upload("/workspace/docs/a.txt", "first\r\nsecond [x]\r\n".encode())
    client.upload("/workspace/docs/nested/b.txt", "another [x]\n".encode())
    client.upload("/workspace/docs/nested/b.bin", b"\xff\x00")
    client.upload("/workspace/docs/picture.png", b"valid-ascii-but-binary")
    client.download_log.clear()
    integrity_checks: list[bool] = []
    backend = AefWorkstationBackend(client, integrity_check=lambda: integrity_checks.append(True))

    assert isinstance(backend, __import__("deepagents.backends.protocol", fromlist=["SandboxBackendProtocol"]).SandboxBackendProtocol)
    assert backend.to_physical_path("/outputs/result.json") == "/workspace/outputs/result.json"
    assert backend.to_virtual_path("/workspace/large_tool_results/a") == "/large_tool_results/a"
    for invalid in ("../escape", "C:/Windows/file", "bad\x00path"):
        with pytest.raises(ValueError):
            backend.to_physical_path(invalid)

    read = backend.read("/docs/a.txt", offset=1, limit=1)
    assert read.file_data is not None and read.file_data["content"] == "second [x]\n"
    backend.read("/docs/a.txt")
    assert client.download_log.count("/workspace/docs/a.txt") == 1
    binary = backend.read("/docs/nested/b.bin")
    assert binary.file_data is not None and binary.file_data["encoding"] == "base64"
    classified_binary = backend.read("/docs/picture.png")
    assert classified_binary.file_data is not None and classified_binary.file_data["encoding"] == "base64"

    assert backend.write("/inputs/new.txt", "x").error is not None
    assert backend.upload_files([("/skills/new.txt", b"x")])[0].error == "permission_denied"
    created = backend.write("/outputs/new.txt", "old")
    assert created.path == "/outputs/new.txt"
    assert backend.write("/outputs/new.txt", "again").error is not None
    edited = backend.edit("/outputs/new.txt", "old", "new")
    assert edited.occurrences == 1
    assert client.files["/workspace/outputs/new.txt"] == b"new"
    client.upload("/workspace/outputs/crlf.txt", b"first\r\nsecond\r\n")
    crlf_edited = backend.edit(
        "/outputs/crlf.txt",
        "first\nsecond\n",
        "first\nupdated\n",
    )
    assert crlf_edited.occurrences == 1
    assert client.files["/workspace/outputs/crlf.txt"] == b"first\nupdated\n"

    assert backend.ls("/missing").entries == []
    docs_entries = backend.ls("/docs").entries
    assert docs_entries is not None
    assert next(item for item in docs_entries if item["is_dir"])["path"] == "/docs/nested/"

    globbed = backend.glob("docs/**/*.txt")
    assert globbed.matches is not None
    assert [item["path"] for item in globbed.matches] == ["/docs/a.txt", "/docs/nested/b.txt"]
    recursive_default = backend.glob("*.txt", "/docs")
    assert recursive_default.matches is not None
    assert [item["path"] for item in recursive_default.matches] == [
        "/docs/a.txt",
        "/docs/nested/b.txt",
    ]
    with pytest.raises(ValueError, match="traversal"):
        backend.glob("../*.txt")
    with pytest.raises(ValueError, match="Backslashes"):
        backend.glob(r"..\*.txt")
    grepped = backend.grep("[x]", "/", "docs/**/*.txt")
    assert grepped.matches is not None
    assert [(item["path"], item["line"]) for item in grepped.matches] == [
        ("/docs/a.txt", 2),
        ("/docs/nested/b.txt", 1),
    ]

    downloaded = backend.download_files(["/docs/nested/b.txt", "/docs/a.txt"])
    assert [item.path for item in downloaded] == ["/docs/nested/b.txt", "/docs/a.txt"]
    response = backend.execute("python inputs/check.py", timeout=0)
    assert response.exit_code == 0 and "warning" in response.output
    assert integrity_checks == [True]
    command, kwargs = client.exec_log[-1]
    assert command[:2] == ["sh", "-lc"]
    assert "PYTHONPATH=/workspace/.harness_runtime" in command[2]
    assert "DEEPAGENT_WORKSPACE_ROOT=/workspace" in command[2]
    assert kwargs["env"] == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    assert backend.execute("true", timeout=301).exit_code == 2

    client.upload("/workspace/.harness_runtime/sitecustomize.py", b"marker = 1\n")
    hidden = backend.glob("*.py", "/")
    assert hidden.matches is not None
    assert "/.harness_runtime/sitecustomize.py" in [item["path"] for item in hidden.matches]

    exact_file = backend.grep("another", "/docs/nested/b.txt", "*.md")
    assert exact_file.matches is not None
    assert [(item["path"], item["line"]) for item in exact_file.matches] == [
        ("/docs/nested/b.txt", 1)
    ]


def test_ls_rejects_response_from_another_subtree() -> None:
    class WrongTreeClient(MemoryClient):
        def list_tree(self, path: str, **kwargs: Any) -> RemoteNode | None:
            del path, kwargs
            return RemoteNode("skills", "/workspace/skills", "dir", None, None)

    with pytest.raises(AefAttemptLost, match="invalid shallow listing"):
        AefWorkstationBackend(WrongTreeClient()).ls("/docs")


def test_aef_backend_discovers_skill_from_parent_directory() -> None:
    client = MemoryClient()
    skill_text = (
        "---\n"
        "name: contract-matrix-review\n"
        "description: Review acquiring contracts.\n"
        "---\n\n"
        "# Contract matrix review\n"
    ).encode()
    client.upload("/workspace/skills/contract-matrix-review/SKILL.md", skill_text)
    backend = AefWorkstationBackend(client)

    skills, error = _list_skills_with_errors(backend, "/skills/")

    assert error is None
    assert [item["name"] for item in skills] == ["contract-matrix-review"]


def _make_staging(root: Path) -> None:
    (root / "inputs").mkdir(parents=True)
    (root / "skills" / "contract-matrix-review").mkdir(parents=True)
    (root / "outputs").mkdir()
    (root / "inputs" / "contract.txt").write_text("contract", encoding="utf-8")
    (root / "inputs" / "matrix.json").write_text("{}", encoding="utf-8")
    (root / "skills" / "contract-matrix-review" / "SKILL.md").write_text("# skill", encoding="utf-8")
    (root / "outputs" / "stale.json").write_text("must not upload", encoding="utf-8")


def test_session_manager_stages_only_sources_and_detects_protected_mutation(tmp_path: Path) -> None:
    _make_staging(tmp_path)
    clients: list[LifecycleClient] = []

    def factory(settings: AefSettings, **kwargs: Any) -> LifecycleClient:
        client = LifecycleClient(settings, **kwargs)
        clients.append(client)
        return client

    settings = AefSettings(heartbeat_sec=999)
    manager = AefSessionManager(settings, client_factory=factory, run_id="run", attempt_no=1)
    backend = manager.start(tmp_path)
    assert backend is manager.backend
    uploaded = set(clients[0].files)
    assert "/workspace/inputs/contract.txt" in uploaded
    assert "/workspace/skills/contract-matrix-review/SKILL.md" in uploaded
    assert "/workspace/.harness_runtime/sitecustomize.py" in uploaded
    assert "/workspace/outputs/stale.json" not in uploaded
    manager.verify_integrity()

    clients[0].files["/workspace/inputs/contract.txt"] = b"tampered"
    with pytest.raises(AefAttemptLost, match="Protected inputs"):
        manager.verify_integrity()
    original_list_tree = clients[0].list_tree
    clients[0].files["/workspace/inputs/contract.txt"] = b"contract"
    clients[0].list_tree = lambda *args, **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AefInfrastructureError("network unavailable")
    )
    with pytest.raises(AefAttemptLost, match="could not be established"):
        manager.verify_integrity()
    clients[0].list_tree = original_list_tree  # type: ignore[method-assign]

    clients[0].ensure_directory("/workspace/skills/rogue-empty")
    with pytest.raises(AefAttemptLost, match="Protected inputs"):
        manager.verify_integrity()
    clients[0].directories.remove("/workspace/skills/rogue-empty")
    manager.verify_integrity()

    deep_extra = (
        "/workspace/skills/" + "/".join(f"level-{index}" for index in range(70)) + "/rogue.txt"
    )
    clients[0].upload(deep_extra, b"unexpected")
    with pytest.raises(AefAttemptLost, match="Protected inputs"):
        manager.verify_integrity()
    assert manager.terminate() is True
    assert clients[0].terminated is True


def test_session_manager_rejects_ttl_shorter_than_requested_profile(tmp_path: Path) -> None:
    _make_staging(tmp_path)

    def factory(settings: AefSettings, **kwargs: Any) -> LifecycleClient:
        client = LifecycleClient(settings, **kwargs)
        client.session = AefSession(
            id="short-ttl",
            token="opaque-token",
            status="ready",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2026, 1, 1, 0, 20, tzinfo=UTC),
            max_file_bytes=20 * 1024 * 1024,
            max_total_bytes=200 * 1024 * 1024,
            exec_timeout_sec=300,
        )
        return client

    manager = AefSessionManager(
        AefSettings(absolute_ttl_sec=3600, heartbeat_sec=999),
        client_factory=factory,
    )
    try:
        with pytest.raises(AefConfigurationError, match="shorter than the requested"):
            manager.start(tmp_path)
    finally:
        manager.terminate()


def test_session_manager_rejects_staging_changed_after_run_snapshot(tmp_path: Path) -> None:
    _make_staging(tmp_path)
    expected: dict[str, tuple[int, str]] = {}
    for path in sorted(
        item
        for root in (tmp_path / "inputs", tmp_path / "skills")
        for item in root.rglob("*")
        if item.is_file()
    ):
        content = path.read_bytes()
        expected["/" + path.relative_to(tmp_path).as_posix()] = (
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
    runtime = Path(__file__).resolve().parents[1] / "harness_runtime" / "sitecustomize.py"
    runtime_content = runtime.read_bytes()
    expected["/.harness_runtime/sitecustomize.py"] = (
        len(runtime_content),
        hashlib.sha256(runtime_content).hexdigest(),
    )
    (tmp_path / "inputs" / "contract.txt").write_text(
        "changed after snapshot",
        encoding="utf-8",
    )

    manager = AefSessionManager(
        AefSettings(heartbeat_sec=999),
        client_factory=LifecycleClient,
    )
    try:
        with pytest.raises(AefConfigurationError, match="changed after the immutable"):
            manager.start(tmp_path, expected_manifest=expected)
    finally:
        manager.terminate()


def test_backend_refuses_operations_after_attempt_deadline() -> None:
    client = MemoryClient()
    client.upload("/workspace/docs/a.txt", b"a")
    backend = AefWorkstationBackend(
        client,
        attempt_check=lambda: (_ for _ in ()).throw(
            AefAttemptLost("safe deadline reached")
        ),
    )

    with pytest.raises(AefAttemptLost, match="deadline"):
        backend.read("/docs/a.txt")


def test_cleanup_never_closes_client_while_heartbeat_is_blocked(tmp_path: Path) -> None:
    _make_staging(tmp_path)
    heartbeat_started = threading.Event()
    release_heartbeat = threading.Event()
    client_closed = threading.Event()
    clients: list[LifecycleClient] = []

    class BlockingHeartbeatClient(LifecycleClient):
        def get_status(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            heartbeat_started.set()
            release_heartbeat.wait(timeout=2)
            return super().get_status()

        def close(self) -> None:
            client_closed.set()

    def factory(settings: AefSettings, **kwargs: Any) -> LifecycleClient:
        client = BlockingHeartbeatClient(settings, **kwargs)
        clients.append(client)
        return client

    manager = AefSessionManager(
        AefSettings(heartbeat_sec=0.01, cleanup_timeout_sec=0.05),
        client_factory=factory,
    )
    manager.start(tmp_path)
    assert heartbeat_started.wait(timeout=1)
    assert manager.terminate() is False
    assert clients[0].terminated is False
    assert client_closed.is_set() is False
    release_heartbeat.set()
    assert client_closed.wait(timeout=1)


def test_ambiguous_mkdir_with_failed_reconciliation_loses_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _mock_client(lambda request: httpx.Response(500, request=request))
    client._session = _session()  # noqa: SLF001
    calls = 0

    def stat(path: str) -> None:
        nonlocal calls
        del path
        calls += 1
        if calls == 1:
            return None
        raise AefInfrastructureError("cannot reconcile")

    request = httpx.Request("POST", TEST_WORKSTATION_URL + "/exec")
    monkeypatch.setattr(client, "stat", stat)
    monkeypatch.setattr(
        client,
        "exec_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("lost", request=request)),
    )
    with pytest.raises(AefAttemptLost, match="ambiguous directory"):
        client.ensure_directory("/workspace/new-dir")


def test_malformed_mkdir_success_is_reconciled_by_stat() -> None:
    list_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal list_calls
        if request.url.path.endswith("/files:list"):
            list_calls += 1
            if list_calls == 1:
                return httpx.Response(404, json={"code": "missing"}, request=request)
            return httpx.Response(
                200,
                json={
                    "name": "new-dir",
                    "path": "/workspace/new-dir",
                    "kind": "dir",
                    "bytes": None,
                    "sha256": None,
                    "children": [],
                },
                request=request,
            )
        return httpx.Response(200, json={"stdout": "", "stderr": ""}, request=request)

    client = _mock_client(handler)
    client._session = _session()  # noqa: SLF001
    client.ensure_directory("/workspace/new-dir")
    assert list_calls == 2


def test_supervisor_restarts_with_fresh_manager_backend_and_thread(tmp_path: Path) -> None:
    _make_staging(tmp_path)
    managers: list[Any] = []

    class FakeManager:
        def __init__(self, settings: AefSettings, **kwargs: Any) -> None:
            del settings
            self.thread_id = kwargs["thread_id"]
            self.attempt_no = kwargs["attempt_no"]
            self.client = SimpleNamespace(session=SimpleNamespace(id=f"session-{self.attempt_no}"))
            self._backend = SimpleNamespace(id=f"session-{self.attempt_no}")
            self.closed = False
            managers.append(self)

        def start(self, staging_root: Path) -> Any:
            assert Path(staging_root) == tmp_path
            return self._backend

        def ensure_healthy(self) -> None:
            pass

        def verify_integrity(self) -> None:
            pass

        def terminate(self) -> bool:
            self.closed = True
            return True

    supervisor: RunSupervisor[str] = RunSupervisor(
        AefSettings(), run_id="run", manager_factory=FakeManager, max_attempts=2
    )
    callbacks: list[tuple[int, str, str]] = []

    def callback(manager: Any, backend: Any, thread_id: str, attempt_no: int) -> str:
        callbacks.append((attempt_no, backend.id, thread_id))
        if attempt_no == 1:
            raise AefAttemptLost("ambiguous exec")
        return "done"

    assert supervisor.run(tmp_path, callback) == "done"
    assert len(managers) == 2
    assert callbacks[0][1] != callbacks[1][1]
    assert callbacks[0][2] != callbacks[1][2]
    assert all(manager.closed for manager in managers)
    assert [report["status"] for report in supervisor.reports] == ["restarting", "complete"]
    assert all(report["cleanup_status"] == "complete" for report in supervisor.reports)


def test_supervisor_cleanup_failure_does_not_mask_required_restart(tmp_path: Path) -> None:
    _make_staging(tmp_path)

    class CleanupFailingManager:
        def __init__(self, settings: AefSettings, **kwargs: Any) -> None:
            del settings
            self.attempt_no = kwargs["attempt_no"]
            self.client = SimpleNamespace(session=SimpleNamespace(id=f"s-{self.attempt_no}"))

        def start(self, staging_root: Path) -> Any:
            del staging_root
            return SimpleNamespace(id=f"s-{self.attempt_no}")

        def ensure_healthy(self) -> None:
            pass

        def verify_integrity(self) -> None:
            pass

        def terminate(self) -> bool:
            if self.attempt_no == 1:
                raise RuntimeError("cleanup broke")
            return True

    supervisor: RunSupervisor[str] = RunSupervisor(
        AefSettings(), manager_factory=CleanupFailingManager, max_attempts=2
    )

    def callback(manager: Any, backend: Any, thread_id: str, attempt_no: int) -> str:
        del manager, backend, thread_id
        if attempt_no == 1:
            raise AefAttemptLost("restart")
        return "recovered"

    assert supervisor.run(tmp_path, callback) == "recovered"
    assert [report["cleanup_status"] for report in supervisor.reports] == ["failed", "complete"]


def test_supervisor_marks_non_restartable_failure_and_records_duration(tmp_path: Path) -> None:
    _make_staging(tmp_path)

    class Manager:
        def __init__(self, settings: AefSettings, **kwargs: Any) -> None:
            del settings, kwargs
            self.client = SimpleNamespace(session=SimpleNamespace(id="session"))

        def start(self, staging_root: Path) -> Any:
            del staging_root
            return SimpleNamespace(id="session")

        def terminate(self) -> bool:
            return True

    supervisor: RunSupervisor[None] = RunSupervisor(
        AefSettings(), manager_factory=Manager, max_attempts=2
    )
    with pytest.raises(RuntimeError, match="quality gate"):
        supervisor.run(
            tmp_path,
            lambda manager, backend, thread_id, attempt_no: (_ for _ in ()).throw(
                RuntimeError("quality gate")
            ),
        )
    assert len(supervisor.reports) == 1
    assert supervisor.reports[0]["status"] == "failed"
    assert supervisor.reports[0]["restart_reason"] == "RuntimeError"
    assert isinstance(supervisor.reports[0]["duration_ms"], int)


def test_writer_preferring_lock_does_not_let_new_reader_overtake_writer() -> None:
    lock = WriterPreferringRWLock()
    first_reader_entered = threading.Event()
    release_first_reader = threading.Event()
    writer_waiting = threading.Event()
    order: list[str] = []

    def first_reader() -> None:
        with lock.read_lock():
            first_reader_entered.set()
            release_first_reader.wait(2)

    def writer() -> None:
        first_reader_entered.wait(2)
        writer_waiting.set()
        with lock.write_lock():
            order.append("writer")
            time.sleep(0.02)

    def second_reader() -> None:
        writer_waiting.wait(2)
        time.sleep(0.02)
        with lock.read_lock():
            order.append("reader")

    threads = [
        threading.Thread(target=first_reader),
        threading.Thread(target=writer),
        threading.Thread(target=second_reader),
    ]
    for thread in threads:
        thread.start()
    assert first_reader_entered.wait(1)
    assert writer_waiting.wait(1)
    time.sleep(0.03)
    release_first_reader.set()
    for thread in threads:
        thread.join(2)
    assert order == ["writer", "reader"]
