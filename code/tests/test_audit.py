import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import DATASET_DIR, REPO_ROOT, new_workdir
from deletion_audit.audit import AuditRun, run_audit
from deletion_audit.llm import LlmClient, TransportReply, Usage
from deletion_audit.model import Verdict

MAIN = REPO_ROOT / "code" / "main.py"
SAMPLE_REQUESTS = DATASET_DIR / "sample_deletion_requests.csv"
INTENT_MODEL, VISUAL_MODEL = "fake-intent", "fake-visual"


class FakeTransport:
    """Every text has Deletion Intent and every visual file is empty: the deterministic
    pipeline is what these tests exercise."""

    def send(self, request) -> TransportReply:
        if request.purpose == "intent":
            data = {"intent": "DELETION", "request_type": "ACCOUNT_DELETION", "language": "en"}
        else:
            data = {"document_kind": "fake", "visible_identifiers": [], "records": []}
        return TransportReply(data, Usage(1, 1, 0), request.model, "STOP")


@pytest.fixture(scope="module")
def cache_dir() -> Path:
    return new_workdir() / "cache"


def audit(requests: Path, cache_dir: Path) -> AuditRun:
    # The fake extractions are empty, so the real corrections overlay (made for the real
    # extractions) is replaced by a missing file.
    return run_audit(DATASET_DIR, requests, LlmClient(cache_dir, FakeTransport()),
                     intent_model=INTENT_MODEL, visual_model=VISUAL_MODEL, require_verified=False,
                     verification_path=cache_dir.parent / "no_verification.json")


@pytest.fixture(scope="module")
def sample_run(cache_dir: Path) -> AuditRun:
    return audit(SAMPLE_REQUESTS, cache_dir)


def test_audit_returns_one_assessed_result_and_trace_per_case(sample_run: AuditRun) -> None:
    run = sample_run

    assert [result.case_id for result in run.results] == [case.case_id for case in run.cases]
    assert len(run.results) == 32
    assert run.failures == ()
    case_05 = next(result for result in run.results if result.case_id == "case_05")
    assert case_05.verdict is Verdict.INCOMPLETE and len(case_05.steps) == 3
    trace = run.traces["case_10"]
    assert trace["subject"]["user_id"] == "user_10"
    assert "nadia.ionescu@yahoo.com" in trace["subject"]["emails"]
    assert {item["resource"] for item in trace["resources"]} >= {"support_platform/support_002"}


def test_unresolvable_email_is_traced_not_failed(workdir: Path, cache_dir: Path) -> None:
    requests = workdir / "requests.csv"
    requests.write_text(
        "case_id,email,requested_at,reported_status,reported_at,audit_at,request_text\n"
        "case_900,nobody@nowhere.test,2026-09-01T00:00:00Z,COMPLETE,2026-09-02T00:00:00Z,"
        "2026-09-03T00:00:00Z,Please delete my account.\n",
        encoding="utf-8",
    )

    run = audit(requests, cache_dir)

    assert run.failures == ()
    assert run.traces["case_900"]["subject"] is None


def run_main(cache_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # --offline means no transport is ever built; the blanked provider and key make sure the
    # repo's own .env cannot point the subprocess at a live model either.
    env = {**os.environ, "DELETION_AUDIT_PROVIDER": "", "GEMINI_API_KEY": "", "ANTHROPIC_API_KEY": "",
           "DELETION_AUDIT_INTENT_MODEL": INTENT_MODEL, "DELETION_AUDIT_VISUAL_MODEL": VISUAL_MODEL}
    return subprocess.run(
        [sys.executable, "-B", str(MAIN), "--offline", "--allow-unverified",
         "--cache-dir", str(cache_dir), "--verification", str(cache_dir.parent / "no_verification.json"), *args],
        capture_output=True, text=True, encoding="utf-8", check=False, env=env, timeout=300,
    )


def test_cli_writes_a_valid_output_file(workdir: Path, cache_dir: Path, sample_run: AuditRun) -> None:
    output = workdir / "output.csv"

    completed = run_main(cache_dir, "--requests", str(SAMPLE_REQUESTS), "--output", str(output),
                         "--trace-dir", str(workdir / "traces"))

    assert completed.returncode == 0, completed.stderr
    rows = list(csv.DictReader(output.open(encoding="utf-8", newline="")))
    assert len(rows) == 32
    assert [row["case_id"] for row in rows[:2]] == ["case_01", "case_02"]
    assert all(json.loads(row["steps_json"]) is not None for row in rows)
    assert json.loads((workdir / "traces" / "case_01.json").read_text(encoding="utf-8"))["case_id"] == "case_01"
    assert "32 rows" in completed.stdout


def test_cli_output_is_byte_identical_across_runs(workdir: Path, cache_dir: Path,
                                                  sample_run: AuditRun) -> None:
    first, second = workdir / "first.csv", workdir / "second.csv"
    for output in (first, second):
        assert run_main(cache_dir, "--requests", str(SAMPLE_REQUESTS), "--output", str(output),
                        "--trace-dir", str(workdir / "traces")).returncode == 0

    assert first.read_bytes() == second.read_bytes()
