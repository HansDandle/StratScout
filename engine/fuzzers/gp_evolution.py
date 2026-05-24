"""
Genetic programming evolution engine.

Designed to run inside an existing multiprocessing worker (one that has already
called _worker_init from walk_forward_etf.py, so wf._histories is populated).

evolve_in_worker() is the entry point for _run_month_gp.
"""
from __future__ import annotations

import random


def evolve_in_worker(
    t_start: str,
    t_mid1: str,
    t_mid2: str,
    t_end: str,
    population_size: int = 100,
    n_generations: int = 30,
) -> tuple[dict | None, float]:
    """
    Run GP evolution using the worker-local histories (wf._histories).

    Returns (best_strategy, best_score).
    Scores candidates on 3 sub-windows using _combined_score (same as random search).
    """
    from stratscout.engine.fuzzers import walk_forward_etf as wf
    from stratscout.engine.fuzzers.strategy_dsl import random_strategy, mutate_strategy, crossover_strategies
    from stratscout.engine.fuzzers.gp_backtest import run_gp_backtest

    histories = wf._histories
    if not histories:
        return None, -999.0

    def _score(strategy: dict) -> float:
        try:
            r1 = run_gp_backtest(strategy, histories, t_start, t_mid1)
            r2 = run_gp_backtest(strategy, histories, t_mid1,  t_mid2)
            r3 = run_gp_backtest(strategy, histories, t_mid2,  t_end)
            return wf._combined_score(
                r1["perf"].get("cagr_pct", 0),
                r2["perf"].get("cagr_pct", 0),
                r3["perf"].get("cagr_pct", 0),
                r1["perf"].get("max_drawdown_pct", 0),
                r2["perf"].get("max_drawdown_pct", 0),
                r3["perf"].get("max_drawdown_pct", 0),
                r1["n_trades"],
                r2["n_trades"],
                r3["n_trades"],
            )
        except Exception:
            return -999.0

    # Seed population
    population: list[dict] = [random_strategy() for _ in range(population_size)]
    best_score: float = -999.0
    best_strategy: dict | None = None

    import os, time as _time
    pid = os.getpid()
    t0 = _time.monotonic()

    for _gen in range(n_generations):
        scored: list[tuple[float, dict]] = [(_score(s), s) for s in population]
        scored.sort(key=lambda x: x[0], reverse=True)

        gen_best_score = scored[0][0]
        gen_best = scored[0][1]
        if gen_best_score > best_score:
            best_score = gen_best_score
            best_strategy = gen_best

        elapsed = _time.monotonic() - t0
        print(f"  [GP pid={pid}] gen {_gen+1}/{n_generations} best={gen_best_score:.3f} elapsed={elapsed:.0f}s", flush=True)

        # Selection: keep top 10% as elites
        n_elite  = max(1, population_size // 10)
        n_mutate = int(population_size * 0.40)
        n_cross  = int(population_size * 0.30)
        n_fresh  = population_size - n_elite - n_mutate - n_cross

        elites = [s for _, s in scored[:n_elite]]
        next_pop: list[dict] = list(elites)

        for _ in range(n_mutate):
            base = random.choice(elites)
            next_pop.append(mutate_strategy(base, strength=random.uniform(0.1, 0.5)))

        pairs = n_cross // 2
        for _ in range(pairs):
            if len(elites) >= 2:
                a, b = random.sample(elites, 2)
            else:
                a = b = elites[0]
            c1, c2 = crossover_strategies(a, b)
            next_pop.extend([c1, c2])

        for _ in range(max(0, n_fresh)):
            next_pop.append(random_strategy())

        population = next_pop[:population_size]

    return best_strategy, best_score
