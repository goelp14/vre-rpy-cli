"""Setup files, diffs, calibration and the replay fingerprint."""

from __future__ import annotations

import json

import numpy as np
import pytest

from _fixtures import TOMATO_SPA, find_fixture
from vre_rpy.setup_files import FIELD_INFO, candidates_for_session, diff_setups, library, load_setup, physical
from vre_rpy.setup_fingerprint import Calibration, compare_fingerprints


def _write_setup(path, arb_f=5, wing=5, bias=44):
    data = {
        "carName": "test_gt3",
        "basicSetup": {"tyres": {"tyreCompound": 0, "tyrePressure": [59, 60, 55, 56]},
                       "alignment": {"camber": [0, 0, 0, 0], "staticCamber": [-4.0, -4.0, -3.5, -3.5]},
                       "strategy": {"fuel": 40}},
        "advancedSetup": {"mechanicalBalance": {"aRBFront": arb_f, "aRBRear": 0, "brakeBias": bias},
                          "aeroBalance": {"rearWing": wing, "rideHeight": [0, 6, 0, 18]}},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_load_physical_and_diff(tmp_path):
    a = load_setup(_write_setup(tmp_path / "test_gt3" / "spa" / "a.json"))
    b = load_setup(_write_setup(tmp_path / "test_gt3" / "spa" / "b.json", arb_f=7, wing=3))
    ph = physical(a)
    assert ph["camber_deg"]["LF"] == -4.0 and ph["fuel_l"] == 40
    assert abs(ph["cold_pressure_psi_convention"]["LF"] - 26.2) < 1e-6
    d = {r["field"]: r for r in diff_setups(a, b)}
    assert d["advancedSetup.mechanicalBalance.aRBFront"]["delta"] == 2
    assert "understeer" in d["advancedSetup.mechanicalBalance.aRBFront"]["increase_means"]
    assert d["advancedSetup.aeroBalance.rearWing"]["delta"] == -2
    assert all("increase" in v for v in FIELD_INFO.values())


def test_library_and_candidates(tmp_path):
    import os, time

    old = _write_setup(tmp_path / "test_gt3" / "Spa" / "old.json")
    new = _write_setup(tmp_path / "test_gt3" / "Spa" / "new.json")
    now = time.time()
    os.utime(old, (now - 3600, now - 3600))
    os.utime(new, (now + 3600, now + 3600))
    lib = library(tmp_path, "test_gt3", "spa")
    assert [s["name"] for s in lib] == ["new", "old"]
    assert [s["name"] for s in candidates_for_session(tmp_path, "test_gt3", "spa", now)] == ["old"]


def test_calibration_sensitivity(tmp_path):
    cal = Calibration(tmp_path / "cal.json")
    for k, wing in enumerate((2, 6)):
        st = load_setup(_write_setup(tmp_path / "s" / f"{k}.json", wing=wing))
        fp = {"estimated": {"aero_front_share": {"value": 0.45 - 0.01 * wing}}}
        cal.add("test_gt3", st, fp, session=f"s{k}")
    sens = {(r["field"], r["item"]): r for r in cal.sensitivities("test_gt3")}
    assert abs(sens[("advancedSetup.aeroBalance.rearWing", "aero_front_share")]["per_click"] + 0.01) < 1e-6


def test_compare_respects_noise():
    a = {"estimated": {"brake_slip_front_share": {"value": 0.52, "spread": 0.01, "status": "uncalibrated"},
                       "aero_front_share": {"value": 0.38, "spread": 0.2, "status": "uncalibrated"}}}
    b = {"estimated": {"brake_slip_front_share": {"value": 0.46, "spread": 0.01, "status": "uncalibrated"},
                       "aero_front_share": {"value": 0.30, "spread": 0.2, "status": "uncalibrated"}}}
    rows = {r["item"]: r for r in compare_fingerprints(a, b)}
    assert "front braking" in rows["brake_slip_front_share"]["verdict"]
    assert rows["aero_front_share"]["verdict"] == "can't tell"


def test_fingerprint_on_fixture():
    p = find_fixture(TOMATO_SPA)
    if p is None:
        pytest.skip("fixture missing")
    from vre_rpy.replay_frames import ReplayFrames
    from vre_rpy.setup_fingerprint import fingerprint

    fp = fingerprint(ReplayFrames(p))
    g = fp["exact"]["kmh_per_1000rpm_by_gear"]
    assert len(g) >= 5 and all(g[str(i)] < g[str(i + 1)] for i in range(1, 5))
    assert abs(fp["exact"]["static_mass_kg"] - 1384) < 15
    assert 0.3 < fp["estimated"]["brake_slip_front_share"]["value"] < 0.8
