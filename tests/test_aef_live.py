"""Opt-in smoke tests for the fixed AEF WorkStation test deployment.

These tests never run implicitly because they create a real disposable
session. Set RUN_AEF_LIVE_TESTS=1 from an environment with corporate
DNS/VPN/IFT access and provide the documented CA/create secret variables when
the deployment requires them.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

from aef_workstation import (
    AefSettings,
    AefSessionManager,
    AefWorkstationBackend,
    AefWorkstationClient,
    TEST_WORKSTATION_URL,
)


pytestmark = [
    pytest.mark.aef_live,
    pytest.mark.skipif(
        os.environ.get("RUN_AEF_LIVE_TESTS") != "1",
        reason="set RUN_AEF_LIVE_TESTS=1 on the corporate network",
    ),
]


def test_live_session_files_posix_python_and_timeout() -> None:
    settings = AefSettings.from_env()
    assert settings.base_url == os.environ.get(
        "AEF_WORKSTATION_BASE_URL", TEST_WORKSTATION_URL
    ).rstrip("/")
    client = AefWorkstationClient(settings)
    try:
        preflight = client.preflight()
        assert preflight["/health/readiness"]
        assert preflight["/version"]
        assert preflight["/openapi.json"]
        session = client.create_session()
        assert session.status == "ready"
        status = client.get_status()
        assert status["id"] == session.id
        assert status["status"] == "ready"

        client.ensure_directory("/workspace/live")
        content = "кириллица\n".encode()
        digest = hashlib.sha256(content).hexdigest()
        assert client.upload("/workspace/live/input.txt", content) == digest
        tree = client.list_tree(
            "/workspace/live",
            recursive=False,
            max_depth=1,
            include_sha256=True,
        )
        assert tree is not None
        assert [child.path for child in tree.children] == [
            "/workspace/live/input.txt"
        ]
        assert client.download("/workspace/live/input.txt").content == content

        runtime = Path(__file__).resolve().parents[1] / "harness_runtime" / "sitecustomize.py"
        client.ensure_directory("/workspace/.harness_runtime")
        client.upload(
            "/workspace/.harness_runtime/sitecustomize.py",
            runtime.read_bytes(),
        )
        client.ensure_directory("/workspace/outputs")
        backend = AefWorkstationBackend(client)
        result = backend.execute(
            "python -c \"from pathlib import Path; "
            "Path('/outputs/live.txt').write_text('готово', encoding='utf-8')\""
        )
        assert result.exit_code == 0, result.output
        assert client.download("/workspace/outputs/live.txt").content.decode() == "готово"

        timeout = backend.execute("sleep 2", timeout=1)
        assert timeout.exit_code == 124
    finally:
        client.terminate()
        client.close()


@pytest.mark.skipif(
    os.environ.get("RUN_AEF_LONG_LIVE_TESTS") != "1",
    reason="set RUN_AEF_LONG_LIVE_TESTS=1 for the >15 minute heartbeat test",
)
def test_live_heartbeat_keeps_attempt_healthy_beyond_idle_ttl(tmp_path: Path) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "skills" / "contract-matrix-review").mkdir(parents=True)
    (tmp_path / "inputs" / "contract.txt").write_text("1. Условие", encoding="utf-8")
    (tmp_path / "inputs" / "matrix.json").write_text("[]", encoding="utf-8")
    (tmp_path / "skills" / "contract-matrix-review" / "SKILL.md").write_text(
        "---\nname: contract-matrix-review\ndescription: live test\n---\n",
        encoding="utf-8",
    )

    manager = AefSessionManager(AefSettings.from_env())
    try:
        manager.start(tmp_path)
        for _ in range(32):
            time.sleep(30)
            manager.check_attempt_active()
        assert manager.ensure_healthy()["status"] == "ready"
    finally:
        manager.terminate()
