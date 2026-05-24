"""StratScout - multi-mode algo trading platform.

Three deployment modes share one engine:
  - Desktop: Tauri shell with bundled Python sidecar
  - Web Free: BYOC local agent + cloud web UI
  - Web Pro: Cloud workers + cloud web UI

Engine code under stratscout.engine is the single source of truth.
"""

__version__ = "0.1.0-dev"
