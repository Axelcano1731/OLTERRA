"""Cada plan dice algo de la OLT: si respondió o si no se pudo entrar."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from olterra.api.state import olt_status_after
from olterra.executor.plan import PlanResult, StepResult


def result(status: str, *oks: bool) -> PlanResult:
    now = datetime.now(UTC)
    return PlanResult(
        plan_id=uuid4(),
        tenant_id=uuid4(),
        olt_id=uuid4(),
        executor="prueba",
        status=status,  # type: ignore[arg-type]
        steps=[StepResult(index=i, ok=ok) for i, ok in enumerate(oks)],
        started_at=now,
        finished_at=now,
    )


def test_any_answer_means_online() -> None:
    assert olt_status_after(result("ok", True, True)) == "online"
    assert olt_status_after(result("partial", True, False)) == "online"  # un comando falló


def test_no_answer_means_unreachable() -> None:
    assert olt_status_after(result("failed", False, False)) == "unreachable"
    assert olt_status_after(result("failed")) == "unreachable"


def test_plans_that_never_left_say_nothing() -> None:
    assert olt_status_after(result("expired")) is None
    assert olt_status_after(result("rejected")) is None
