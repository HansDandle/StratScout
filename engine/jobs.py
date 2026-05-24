"""Job execution abstraction.

The fuzzers + walk-forward validators all want the same thing: map a callable
over a stream of param dicts, collect results, accept a worker initializer.

Today that's `multiprocessing.Pool`. Tomorrow it's Modal `.spawn_map` for the
Pro tier or Redis-stream dispatch for the BYOC agent. By going through this
interface, fuzzer code stays unchanged across deployment modes.

Usage:
    runner = make_runner()  # picks LocalPoolRunner unless STRATSCOUT_RUNNER=modal
    with runner.session(workers=4, init_fn=load_data, init_args=()) as session:
        for result in session.imap_unordered(run_one, param_stream):
            handle(result)
"""
from __future__ import annotations

import multiprocessing as mp
import os
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Protocol


class _Session(Protocol):
    def imap_unordered(self, fn: Callable[[Any], Any], items: Iterable[Any], chunksize: int = 1) -> Iterator[Any]: ...


class JobRunner(Protocol):
    """Common interface for local-pool and cloud-worker job execution."""

    @contextmanager
    def session(
        self,
        workers: int,
        init_fn: Callable[..., None] | None = None,
        init_args: tuple = (),
    ) -> Iterator[_Session]:
        ...


class LocalPoolRunner:
    """Wraps multiprocessing.Pool. Matches the legacy fuzzer execution model."""

    @contextmanager
    def session(
        self,
        workers: int,
        init_fn: Callable[..., None] | None = None,
        init_args: tuple = (),
    ) -> Iterator[mp.pool.Pool]:
        pool = mp.Pool(
            processes=workers,
            initializer=init_fn,
            initargs=init_args,
        )
        try:
            yield pool
        finally:
            pool.close()
            pool.join()


class SerialRunner:
    """Single-process runner — useful for tests and tiny jobs where Pool overhead dominates."""

    class _SerialSession:
        def __init__(self, init_fn: Callable[..., None] | None, init_args: tuple):
            if init_fn is not None:
                init_fn(*init_args)

        def imap_unordered(self, fn: Callable[[Any], Any], items: Iterable[Any], chunksize: int = 1) -> Iterator[Any]:
            for item in items:
                yield fn(item)

    @contextmanager
    def session(
        self,
        workers: int,
        init_fn: Callable[..., None] | None = None,
        init_args: tuple = (),
    ) -> Iterator[_SerialSession]:
        yield self._SerialSession(init_fn, init_args)


def make_runner() -> JobRunner:
    """Factory. Reads STRATSCOUT_RUNNER env var.

    Values:
        "local" (default) - multiprocessing.Pool on this machine
        "serial"          - no parallelism (tests / debugging)
        "modal"           - cloud workers (Phase 3 — not yet implemented)
    """
    kind = os.environ.get("STRATSCOUT_RUNNER", "local").lower()
    if kind == "serial":
        return SerialRunner()
    if kind == "modal":
        raise NotImplementedError("Modal runner ships in Phase 3 (web Pro tier)")
    return LocalPoolRunner()
