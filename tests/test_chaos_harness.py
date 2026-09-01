"""Runs the chaos harness (PROJECT_SPEC.md section 12) for one trial per
kill point — enough to prove the mechanism works, not the full pass-rate
table `python -m src.chaos.harness` produces for the README. Needs a live
Postgres and starts its own out-of-process mock services (plain uvicorn
subprocesses); skipped when neither is available."""

from src.chaos.harness import run_harness


async def test_all_five_kill_points_pass_at_least_once(postgres_dsn):
    report = run_harness(postgres_dsn, trials=1)

    assert len(report.results) == 5
    failures = [r for r in report.results if not r.passed]
    assert not failures, "\n".join(f"{r.name}: {r.detail}" for r in failures)
