"""Proves the approval API's core requirement literally:
"Approvals must be actionable from a different machine than the one
running the agent." Each half runs as its own OS subprocess, building its
own independent FastAPI app instance; the only thing they share is the
Postgres DSN. Skipped when no Postgres is reachable (this environment
does not run docker-compose); the in-process route logic is covered by
test_api.py."""

import json
import subprocess
import sys
import uuid
from pathlib import Path

HELPERS = Path(__file__).parent / "_subprocess_helpers"


def _run(script: str, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(HELPERS / script), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"{script} failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


async def test_decision_submitted_by_a_separate_process_resumes_the_run(postgres_dsn):
    run_id = f"api-cross-process-{uuid.uuid4()}"

    created = _run("api_create_run.py", postgres_dsn, run_id)
    assert created["run_id"] == run_id
    assert created["status"] == "AWAITING_APPROVAL"

    resumed = _run("api_submit_decision.py", postgres_dsn, run_id)
    assert resumed["status"] == "COMPLETED"
