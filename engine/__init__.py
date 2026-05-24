"""StratScout engine - pure compute layer shared by all deployment modes.

Subpackages:
  backtest/  - backtest runners (etf, smallcap, core utilities)
  fuzzers/   - parameter search (etf_fuzz, smallcap_fuzz, walk_forward)
  brokers/   - BrokerAdapter interface and Schwab/Alpaca implementations
  data/      - symbol universes, feather I/O, data downloaders
  plots/     - Plotly figure builders (return JSON, framework-agnostic)
  jobs/      - JobRunner abstraction (local multiprocessing or cloud workers)
"""
