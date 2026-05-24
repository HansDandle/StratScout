"""Tests for the JobRunner abstraction."""
from __future__ import annotations

import os

import pytest


def _square(x: int) -> int:
    return x * x


def test_serial_runner():
    from stratscout.engine.jobs import SerialRunner
    runner = SerialRunner()
    with runner.session(workers=1) as session:
        results = list(session.imap_unordered(_square, [1, 2, 3, 4]))
    assert sorted(results) == [1, 4, 9, 16]


def test_serial_runner_init_fn():
    from stratscout.engine.jobs import SerialRunner
    seen = []

    def init(x):
        seen.append(x)

    runner = SerialRunner()
    with runner.session(workers=1, init_fn=init, init_args=("hello",)) as session:
        list(session.imap_unordered(_square, [1]))

    assert seen == ["hello"]


def test_factory_picks_local_by_default(monkeypatch):
    monkeypatch.delenv("STRATSCOUT_RUNNER", raising=False)
    from stratscout.engine.jobs import LocalPoolRunner, make_runner
    runner = make_runner()
    assert isinstance(runner, LocalPoolRunner)


def test_factory_picks_serial_when_env_set(monkeypatch):
    monkeypatch.setenv("STRATSCOUT_RUNNER", "serial")
    from stratscout.engine.jobs import SerialRunner, make_runner
    runner = make_runner()
    assert isinstance(runner, SerialRunner)


def test_factory_rejects_modal_until_implemented(monkeypatch):
    monkeypatch.setenv("STRATSCOUT_RUNNER", "modal")
    from stratscout.engine.jobs import make_runner
    with pytest.raises(NotImplementedError):
        make_runner()
