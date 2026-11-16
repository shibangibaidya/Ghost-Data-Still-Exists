"""Shared fixtures.

`workdir` replaces pytest's `tmp_path`: the system temp folder is not writable in every
environment, and pytest's own cleanup of old temp folders is not wanted here. Folders are
created under the git-ignored `.test_tmp/` at the repository root and never removed.

Tests never use the network: any socket connection fails at once, so a live provider call
that slips into a test is an error instead of a hang. CLI subprocesses run with `--offline`.
"""

import socket
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"
TEST_TMP = REPO_ROOT / ".test_tmp"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise OSError("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def new_workdir() -> Path:
    TEST_TMP.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(dir=TEST_TMP))


@pytest.fixture
def workdir() -> Path:
    return new_workdir()
