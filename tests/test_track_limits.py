"""Track limits: excursion detection, zone model, attribution (synthetic + Spa fixture)."""

from __future__ import annotations

import numpy as np

from _fixtures import TOMATO_SPA, find_fixture
from vre_rpy.track_limits import ZoneModel, attribute, excursions, legal_envelope


def _lap(valid=True, inv_ms=None, bumps=()):
    t = np.arange(0, 120, 0.05)
    s = t * 50.0
    any_l = np.full(len(t), -3.0)
    all4_l = np.full(len(t), -5.0)
    for t0, peak in bumps:  # each bump: wheels go beyond the left edge around t0
        m = np.abs(t - t0) < 0.5
        any_l[m] = 0.5
        all4_l[m] = peak
    z = np.full(len(t), -4.0)
    return {
        "t": t.tolist(), "s_m": s.tolist(),
        "edge_any_left_m": any_l.tolist(), "edge_all4_left_m": all4_l.tolist(),
        "edge_any_right_m": z.tolist(), "edge_all4_right_m": z.tolist(),
        "lap_valid_so_far": (t < (inv_ms or 1e9) / 1000.0).astype(float).tolist(),
        "meta": {"valid": valid, "invalid_at_lap_ms": inv_ms, "lap_index": 1, "lap_ms": 120000},
    }


def test_excursions_found_with_side_and_all4():
    ex = excursions(_lap(bumps=[(30.0, 0.4), (80.0, -0.3)]))
    assert [round(e["peak_s_m"] / 50) for e in ex] == [30, 80]
    assert [e["all4"] for e in ex] == [True, False]
    assert all(e["side"] == "left" for e in ex)


def test_delayed_verdict_uses_zone_model_over_largest_excursion():
    # cut at t=30 judged 11 s later; a bigger excursion at t=38 was forgiven
    lap = _lap(valid=False, inv_ms=41_000, bumps=[(30.0, -0.5), (38.0, 0.9)])
    model = ZoneModel("synthetic")
    for _ in range(4):
        model.add(drop_s=41.0 * 50, exc_s=30.0 * 50, lag_s=11.0)
    att = attribute(lap, excursions(lap), model)
    assert att["method"] == "zone_model" and abs(att["cut_m"] - 1500.0) < 30
    assert abs(att["judged_after_s"] - 11.0) < 0.6
    att0 = attribute(lap, excursions(lap), None)
    assert abs(att0["cut_m"] - 1900.0) < 30  # without history: largest excursion


def test_legal_envelope_only_counts_valid_part():
    lap = _lap(valid=False, inv_ms=60_000, bumps=[(30.0, 0.4), (90.0, 2.0)])
    env = legal_envelope([lap], 6000.0)
    b30, b90 = int(1500 // 10), int(4500 // 10)
    assert env["left"][b30] == 0.4
    assert env["left"][b90] is None  # only reached after the lap was invalid: no legal evidence


def test_spa_fixture_attribution():
    p = find_fixture(TOMATO_SPA)
    if p is None:
        import pytest

        pytest.skip("Spa fixture missing")
    from vre_rpy.replay_frames import ReplayFrames
    from vre_rpy.track_limits import analyze

    rf = ReplayFrames(p)
    if rf.line is None:
        import pytest

        pytest.skip("fastlane.ai not installed")
    rep = analyze([rf])
    laps = rep["drivers"][0]["laps"]
    invalid = [l for l in laps if not l["valid"]]
    assert invalid, "fixture has invalid laps"
    attributed = [l for l in invalid if l["cut"] and l["cut"].get("cut_m") is not None]
    assert len(attributed) >= 0.8 * len(invalid)
    for l in attributed:
        assert 0 <= l["cut"]["judged_after_s"] <= 15.5
        assert any(e["status"] == "judged" for e in l["excursions"])
