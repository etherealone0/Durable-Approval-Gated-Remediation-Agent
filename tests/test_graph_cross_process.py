"""Proves the section 5 claim literally: a workflow suspended by one
process is resumed by a completely separate process, using only its
thread_id, against a real AsyncPostgresSaver. Each half runs as its own
OS subprocess (tests/_subprocess_helpers/) so there is no shared Python
object, event loop, or connection between suspending and resuming.

Skipped when no Postgres is reachable (this environment does not run
docker-compose); see test_graph_control_flow.py for the InMemorySaver
unit tests of the same control flow."""

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


async def test_workflow_resumed_by_separate_process_via_thread_id(postgres_dsn):
    thread_id = f"cross-process-{uuid.uuid4()}"

    suspended = _run("start_and_suspend.py", postgres_dsn, thread_id)
    assert suspended == {"status": "AWAITING_APPROVAL", "suspended": True}

    resumed = _run(
        "resume_from_thread.py",
        postgres_dsn,
        thread_id,
        json.dumps({"decision": "approved", "approver_id": "bob"}),
    )
    assert resumed == {
        "status": "COMPLETED",
        "final_state": "COMPLETED",
        "approval_decision": "approved",
        "approver_id": "bob",
    }
