"""Shared test helpers.

Real ACC replays / MoTeC logs are never committed (they contain other drivers' data).
Point ``VRE_RPY_FIXTURES`` at one or more local directories (``os.pathsep``-separated)
holding them; tests that need a file skip when it is not found.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pytest

TOMATO_SPA = "ACC_261118-203124_O_ferrari_296_gt3_spa.rpy"
ERIK_SPA = "ACCReplay_240418-223443_Q_Spa_ERIK.rpy"
TOMATO_SPA_LD = "Spa-ferrari_296_gt3-10-2026.11.18-20.31.23.ld"


def fixture_dirs() -> list[Path]:
    raw = os.environ.get("VRE_RPY_FIXTURES", "")
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def find_fixture(name: str) -> Optional[Path]:
    for d in fixture_dirs():
        p = d / name
        if p.is_file():
            return p
    return None


def need_fixture(name: str) -> Path:
    p = find_fixture(name)
    if p is None:
        pytest.skip(f"fixture {name} not found (set VRE_RPY_FIXTURES)")
    return p


def fixture_or_missing(name: str) -> Path:
    """Path to a local fixture, or a non-existent path so ``skipif(not p.is_file())`` works."""
    return find_fixture(name) or Path("/nonexistent") / name
