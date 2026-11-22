"""Frame clock, projection and per-lap frames: synthetic checks + Spa golden values."""

from __future__ import annotations

import json

import numpy as np
import pytest

from _fixtures import ERIK_SPA, TOMATO_SPA, need_fixture
from vre_rpy.frame_clock import fit_lag_field, fit_pose_clock, spline_wraps
from vre_rpy.lap_validity import decode_lap_validity
from vre_rpy.track_projection import TrackLine


# ---------------------------------------------------------------- synthetic helpers
def _synthetic_session(lap_s, hz=26.7, gap_after=None, gap_s=0.0, t_start=5.0):
    """Pose spline + timer laps for laps of the given durations (optionally a recording pause)."""
    spl, times = [], []
    t = t_start
    sf = []
    for i, d in enumerate(lap_s):
        sf.append(t)
        n = int(round(d * hz))
        spl += list(np.linspace(0.0, 0.999, n, endpoint=False))
        times += list(t + np.arange(n) / hz)
        t += d
        if gap_after is not None and i == gap_after:
            t += gap_s  # timer keeps running, pose does not record
    laps = [{"lap_index": 0, "session_start_s": t_start - 30.0, "sf_time_s": t_start - 30.0}]
    for i, s0 in enumerate(sf):
        laps.append({"lap_index": i + 1, "session_start_s": s0 + 0.1, "sf_time_s": s0})
    pre = list(np.linspace(0.5, 0.999, int(10 * hz), endpoint=False))  # out-lap tail before first S/F
    spl = pre + spl
    times = list(t_start - 10 + np.arange(len(pre)) / hz) + times
    return np.array(spl), laps, np.array(times)


def test_pose_clock_recovers_times():
    spl, laps, true_t = _synthetic_session([120.0, 118.5, 119.2, 121.0, 118.9])
    clk = fit_pose_clock(spl, laps, span_s=(true_t[0], true_t[-1]))
    assert clk["method"] == "lap_gap_match"
    assert abs(clk["hz"] - 26.7) < 0.05
    w = spline_wraps(spl)
    assert np.max(np.abs(clk["t"][w] - true_t[w])) < 0.05


def test_pose_clock_survives_recording_pause():
    spl, laps, true_t = _synthetic_session([120.0, 118.5, 119.2, 121.0], gap_after=1, gap_s=300.0)
    clk = fit_pose_clock(spl, laps, span_s=(true_t[0], true_t[-1]))
    w = spline_wraps(spl)
    assert np.median(np.abs(clk["t"][w] - true_t[w])) < 0.05


def test_pose_clock_similar_laps_not_shifted_by_a_lap():
    # three near-identical laps: duration matching alone is ambiguous by whole laps
    spl, laps, true_t = _synthetic_session([121.7, 121.9, 121.1])
    clk = fit_pose_clock(spl, laps, span_s=(true_t[0], true_t[-1]))
    assert abs(clk["t"][0] - true_t[0]) < 1.0


def test_lag_field_recovers_offset():
    hz = 25.0
    t = np.arange(0, 600, 1 / hz)
    v = 40 + 25 * np.sin(t / 7.0) + 10 * np.sin(t / 2.3)  # m/s
    s = np.cumsum(v / hz)
    x, z = np.cos(s / 900.0) * 900.0, np.sin(s / 900.0) * 900.0
    lag_true = 0.62
    input_t = np.arange(0, 600, 1 / 11.0)
    speed_kmh = np.interp(input_t - lag_true, t, v) * 3.6
    out = fit_lag_field(x, z, t, input_t, speed_kmh)
    assert out["method"] == "windowed"
    assert abs(np.median(out["lag"]) - lag_true) < 0.05
    assert out["sync_go"]


def test_projection_on_a_circle():
    ang = np.linspace(0, 2 * np.pi, 720, endpoint=False)
    c = np.c_[np.cos(ang), np.sin(ang)] * 100.0
    left = np.c_[np.cos(ang), np.sin(ang)] * 95.0   # left edge inside (counter-clockwise lap)
    right = np.c_[np.cos(ang), np.sin(ang)] * 105.0
    line = TrackLine(c, left, right)
    pr = line.project(np.array([[0.0, 103.0], [-97.0, 0.0]]))
    assert abs(pr["s_m"][0] - np.pi / 2 * 100.0) < 1.0
    assert pr["lat_m"][0] < 0 and pr["lat_m"][1] > 0
    b = line.beyond_edge(np.array([[0.0, 106.0], [0.0, 100.0]]))
    assert b["right"][0] > 0.5 and b["right"][1] < 0 and b["left"][1] < 0


def _timer_records(laps_ms, rec_hz=4.0, markers=True):
    rows, t = [], 10.0
    for lap in laps_ms:
        for ms in np.arange(50, lap, 1000 / rec_hz):
            rows.append((int(ms), 0x7FFFFFFF, 1, 0, t + ms / 1000.0))
        if markers:
            rows.append((int(lap), 0, 0, 0, t + lap / 1000.0 + 0.05))
        t += lap / 1000.0
    buf = bytearray()
    for lap_ms, ref, valid, c, ts in rows:
        buf += np.array([lap_ms, ref], "<u4").tobytes() + bytes([valid, 0, 0, 0]) + np.array([c], "<u4").tobytes()
        buf += np.array([ts], "<f8").tobytes()
    return bytes(buf)


def test_completed_fallback_without_markers():
    laps = decode_lap_validity(_timer_records([120000, 119000, 121000], markers=False))
    assert [l["completed"] for l in laps] == [True, True, False]
    assert all(abs(l["sf_time_s"] - l["session_start_s"] + 0.05) < 0.02 for l in laps)


# ---------------------------------------------------------------- Spa goldens
def _brake_and_minv(fr):
    s = np.asarray(fr["s_m"], float)
    g = np.arange(200, 520, 0.5)
    b = np.interp(g, s, np.asarray(fr["brake"], float))
    v = np.interp(g, s, np.asarray(fr["speed_kmh"], float))
    w = (g > 300) & (g < 420)
    return g[np.argmax(b > 0.95)], v[w].min(), g[w][np.argmin(v[w])]


def test_erik_best_lap_frames_golden():
    from vre_rpy.replay_frames import ReplayFrames

    rf = ReplayFrames(need_fixture(ERIK_SPA))
    if rf.line is None:
        pytest.skip("ACC fastlane.ai not installed (set VRE_ACC_CACHE)")
    assert rf.ident["track"] == "spa" and rf.ident["car"] == "ferrari_296_gt3"
    best = rf.best_lap_index()
    fr = rf.lap_frames(best)
    m = fr["meta"]
    assert m["lap_ms"] == 135425 and m["valid"] is True
    assert m["sync"]["lap_sync_go"] and m["sync"]["lap_r"] > 0.99
    brake_full, vmin, s_vmin = _brake_and_minv(fr)
    assert abs(brake_full - 242.5) < 3.0
    assert abs(vmin - 63.3) < 0.6 and abs(s_vmin - 356.5) < 3.0
    wb = np.asarray(m["wheel_body_m"])
    assert abs((wb[0, 0] - wb[2, 0]) - 2.66) < 0.03          # wheelbase
    assert abs((wb[0, 1] - wb[1, 1]) - 1.73) < 0.03          # front track
    assert len(json.dumps(fr)) < 900_000


def test_tomato_best_lap_frames_golden():
    from vre_rpy.replay_frames import ReplayFrames

    rf = ReplayFrames(need_fixture(TOMATO_SPA))
    if rf.line is None:
        pytest.skip("ACC fastlane.ai not installed (set VRE_ACC_CACHE)")
    fr = rf.lap_frames(rf.best_lap_index())
    assert fr["meta"]["lap_ms"] == 139072
    brake_full, vmin, s_vmin = _brake_and_minv(fr)
    assert abs(brake_full - 246.5) < 3.0
    assert abs(vmin - 64.8) < 0.6 and abs(s_vmin - 361.0) < 3.0


def test_erik_pose_segments_match_correct_laps():
    from vre_rpy.pose_track import best_segment_index, decode_rpy_pose_track

    dec = decode_rpy_pose_track(need_fixture(ERIK_SPA))
    times = [s.get("lap_ms") for s in dec["lap_segments"] if s.get("lap_ms")]
    assert 135425 in times
    best = dec["lap_segments"][best_segment_index(dec)]
    assert best["lap_ms"] == 135425 and best["valid_flag"] is True


def test_new_lanes_tomato():
    from vre_rpy.arr30_decode import decode_rpy_inputs

    dec = decode_rpy_inputs(need_fixture(TOMATO_SPA))
    v = dec["speed_kmh"] / 3.6
    mag = np.sqrt(dec["vel_x"] ** 2 + dec["vel_y"] ** 2 + dec["vel_z"] ** 2)
    m = v > 5
    assert np.corrcoef(mag[m], v[m])[0, 1] > 0.9999
    assert abs(np.median(mag[m] / v[m]) - 1.0) < 0.002
    assert abs(dec["wheel_radius_m"]["LF"] - 0.34) < 1e-3 and abs(dec["wheel_radius_m"]["RR"] - 0.3525) < 1e-3
    assert abs(np.median(dec["g_vert"])) < 0.1
