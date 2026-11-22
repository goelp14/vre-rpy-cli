import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from _fixtures import ERIK_SPA, TOMATO_SPA, find_fixture


@pytest.fixture(scope="session")
def spa_pair_export(tmp_path_factory):
    """One Main + Ref Spa export shared by tests (needs both fixtures + ldparser)."""
    tom, erik = find_fixture(TOMATO_SPA), find_fixture(ERIK_SPA)
    if not (tom and erik):
        pytest.skip("Spa pair fixtures not found (set VRE_RPY_FIXTURES)")
    try:
        from vre_rpy.export_rpy_analysis import run
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"export unavailable: {exc}")
    out = tmp_path_factory.mktemp("spa_pair")
    try:
        run([(tom, "Main"), (erik, "Ref")], out)
    except SystemExit as exc:
        pytest.skip(f"export needs ldparser: {exc}")
    return out
