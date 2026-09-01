"""Exercises src/eval/load_test.py's run_load_test at a small N with
InMemorySaver (always runnable, no Docker needed) to prove the mechanics:
every workflow suspends, a burst resume completes all of them with no
lost state, and latency percentiles come back well-formed. The real
N=100/500/1000 numbers for the README come from
`python -m src.eval.load_test`, and the Postgres-backed connection-count
path additionally needs a live database (skipped here for the same
reason every other Postgres-dependent test in this suite is)."""

from __future__ import annotations

from src.eval.load_test import run_load_test


async def test_small_load_test_suspends_and_resumes_every_run():
    report = await run_load_test(20, concurrency=10)

    assert report.n == 20
    assert report.all_suspended is True
    assert report.suspended_count == 20
    assert report.state_intact is True
    assert report.completed_count == 20
    assert report.checkpointer == "in-memory"


async def test_load_test_reports_memory_and_latency():
    report = await run_load_test(10, concurrency=5)

    assert report.bytes_per_run is not None
    assert report.resume_latency_ms["p50"] >= 0
    assert report.resume_latency_ms["p99"] >= report.resume_latency_ms["p50"]
