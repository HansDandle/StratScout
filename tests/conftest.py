"""Shared pytest fixtures."""
from __future__ import annotations

import random

import pytest


@pytest.fixture
def fixed_seed():
    """Seed `random` deterministically so param generation is reproducible."""
    random.seed(42)
    yield 42
