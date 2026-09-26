"""Shared helpers for lab self-tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lab.runner.clock import LabClock, LabRng
from lab.runner.engine import RunOptions, run_sync
from lab.runner.events import EventJournal

FROZEN = 1790294400


def run_case(target_id: str, case_id: str, *, seed: int = 42, run_id: str = "t") -> object:
    return run_sync(
        RunOptions(
            target_id=target_id,
            case_ids=[case_id],
            seed=seed,
            run_id=run_id,
            frozen_clock=FROZEN,
        )
    )


def run_suite(target_id: str, suite_id: str, *, seed: int = 42, run_id: str = "t") -> object:
    return run_sync(
        RunOptions(
            target_id=target_id,
            suite_id=suite_id,
            seed=seed,
            run_id=run_id,
            frozen_clock=FROZEN,
        )
    )


@pytest.fixture
def clock() -> LabClock:
    return LabClock(frozen_at=float(FROZEN))


@pytest.fixture
def rng() -> LabRng:
    return LabRng(42)


@pytest.fixture
def journal(clock: LabClock) -> EventJournal:
    return EventJournal("test-run", clock)


@pytest.fixture
def env_ns(journal: EventJournal) -> SimpleNamespace:
    return SimpleNamespace(journal=journal, can_observe=False)
