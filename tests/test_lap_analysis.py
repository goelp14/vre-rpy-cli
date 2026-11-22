"""Analysis core: synthetic lap events + Spa pair goldens."""

from __future__ import annotations

import numpy as np
import pytest

from _fixtures import ERIK_SPA, TOMATO_SPA, find_fixture
from vre_rpy.lap_analysis import corner_events, theoretical_best, variance_cliffs


def _lap(brake_ramp_m=12.0, rebrake=False):
    s = np.arange(0, 600, 2.0)
    v = np.where(s < 250, 210.0, np.interp(s, [250, 360, 600], [210.0, 63.0, 160.0]))
    b = np.zeros(len(s))
    b[(s >= 250) & (s < 340)] = np.clip((s[(s >= 250) & (s < 340)] - 250) / brake_ramp_m, 0, 1)
    if rebrake:
        b[(s >= 330) & (s < 345)] = 0.1
        b[(s >= 345) & (s < 355)] = 0.45
    th = np.where(s > 365, 1.0, 0.0)
    steer = np.where((s > 320) & (s < 400), 120.0, 2.0)
    t = np.r_[0, np.cumsum(np.diff(s) / (v[1:] / 3.6))]
    return {"s": s, "t": t, "speed_kmh": v, "brake": b, "throttle_masked": th, "steer_deg": steer}


WIN = {"s0_m": 200.0, "s1_m": 590.0, "apex_m": 360.0, "direction": "right", "corner": "Hairpin", "index": 0}


def test_brake_ramp_and_rebrake_detected():
    fast = corner_events(_lap(brake_ramp_m=12.0), WIN)
    slow = corner_events(_lap(brake_ramp_m=40.0, rebrake=True), WIN)
    assert abs(fast["brake_on_m"] - 252) < 4 and abs(fast["brake_ramp_m"] - 12) < 4
    assert slow["brake_ramp_m"] > 30
    assert slow["rebrake_peak"] > 0.3 > fast["rebrake_peak"]
    assert abs(fast["vmin_kmh"] - 63) < 1 and fast["full_throttle_m"] >= 366


def test_cliffs_and_theoretical_best():
    d = np.r_[np.zeros(100), np.linspace(0, 0.5, 60), np.full(140, 0.5)]
    cl = variance_cliffs(d)
    assert cl and 190 <= cl[0]["s0_m"] <= 230 and cl[0]["loss_s"] > 0.3
    a = _lap()
    b = dict(a); b["t"] = a["t"] * 1.01
    tb = theoretical_best([a, b], [int(a["t"][-1] * 1000), int(b["t"][-1] * 1000)], 598.0)
    assert tb["ok"] and tb["theoretical_best_s"] <= tb["best_lap_s"] + 0.05


def test_spa_pair_analysis_golden():
    me_p, ref_p = find_fixture(TOMATO_SPA), find_fixture(ERIK_SPA)
    if not (me_p and ref_p):
        pytest.skip("Spa pair fixtures missing")
    from vre_rpy.lap_analysis import run_analysis
    from vre_rpy.replay_frames import ReplayFrames

    me, ref = ReplayFrames(me_p), ReplayFrames(ref_p)
    if me.line is None:
        pytest.skip("fastlane.ai not installed")
    me.label, ref.label = "Main", "Ref"
    out = run_analysis(me, ref)
    assert out["ok"] and out["distance_origin"].startswith("metres from the S/F")
    assert abs(out["drivers"]["Ref"]["best_lap_ms"] - 135425) == 0
    ls = [k for k in out["drivers"]["Ref"]["corners"] if k.startswith("La Source")][0]
    r = out["drivers"]["Ref"]["corners"][ls]["best_lap"]
    m = out["drivers"]["Main"]["corners"][ls]["best_lap"]
    assert r["brake_ramp_m"] < m["brake_ramp_m"]          # reference reaches full pressure quicker
    # section losses (contiguous brake-to-brake windows) account for the lap gap
    losses = sum(x["loss_s"] for x in out["corner_loss_best_vs_ref"])
    assert abs(losses - out["delta_best_vs_ref"]["final_s"]) < 0.6
    assert 0 < r["apex_margin_m"] < 5                      # inside-edge margin is plausible at a hairpin
    cs = out["car_state"]["Main"]
    assert abs(cs["mass_kg"] - 1384) < 15 and abs(cs["wheelbase_m"] - 2.66) < 0.03
    assert out["delta_best_vs_ref"]["final_s"] > 0


def test_windows_contain_their_apex_and_never_steal_braking():
    import numpy as np

    from vre_rpy.lap_analysis import brake_points, corner_windows

    s = np.arange(0, 3000, 2.0)
    brake = np.zeros_like(s)
    brake[(s > 900) & (s < 1000)] = 1.0      # braking for the corner at 1050 only
    corners = [{"name": "A", "apex_m": 1050.0}, {"name": "B (flat)", "apex_m": 1300.0}, {"name": "C", "apex_m": 1400.0}]
    ref = {"s": s, "brake": brake}
    assert brake_points(ref, corners, 3000.0) == [902.0, None, None]
    for w in corner_windows(ref, corners, 3000.0):
        assert w["s0_m"] < w["apex_m"] < w["s1_m"] or w["index"] == len(corners) - 1


def test_theoretical_best_ignores_position_jitter():
    """Same pace every lap, a few metres of along-line jitter: the ideal lap must not invent time."""
    rng = np.random.default_rng(1)
    L = 7000.0
    s = np.arange(0.0, L, 2.0)
    t = s / 60.0  # 60 m/s constant: lap 116.667 s
    laps, ms = [], []
    for _ in range(9):
        laps.append({"s": s, "t": np.interp(s + rng.normal(0, 1.0, len(s)), s, t)})
        ms.append(int(round(L / 60.0 * 1000)))
    sectors = np.linspace(0, L, 15)[1:-1]
    tb = theoretical_best(laps, ms, L, sectors_m=sectors)
    assert tb["ok"] and tb["method"] == "corner_sectors" and len(tb["sectors"]) == 14
    assert tb["potential_s"] < 0.6, tb
    fixed = theoretical_best(laps, ms, L)
    assert fixed["method"] == "fixed_sectors" and fixed["potential_s"] < 0.6
    # the old 50 m slicing turns the same jitter into seconds of phantom pace
    assert theoretical_best(laps, ms, L, sectors_m=np.arange(50.0, L, 50.0))["potential_s"] > 3 * tb["potential_s"]


def test_pedal_handover_events():
    lap = _lap(brake_ramp_m=12.0)
    s = lap["s"]
    lap["input_dt_s"] = 0.04
    # left-foot: throttle stays past half for 10 m after the brake starts at 250 m
    lap["throttle"] = np.where(s < 262, 1.0, np.where(s > 365, 1.0, 0.0))
    ev = corner_events(lap, WIN)
    assert ev["brake_overlap_s"] > 0.15 and ev["lift_gap_s"] == 0.0 and ev["brake_at_lift"] > 0.9
    # right-foot with a pause: lift 20 m before the brake
    lap["throttle"] = np.where(s < 230, 1.0, np.where(s > 365, 1.0, 0.0))
    ev = corner_events(lap, WIN)
    assert ev["brake_overlap_s"] == 0.0 and 0.25 < ev["lift_gap_s"] < 0.45
