"""MoTeC export v2: pose channels, wrap-safe lap distance, estimates, worksheet set."""

from __future__ import annotations

import numpy as np
import pytest

from vre_rpy.rpy_motec_ld import _fill_gaps, resample_uniform


def test_lap_distance_resample_keeps_wrap_sharp():
    t = np.arange(0, 30, 0.09)
    lap = np.mod(t * 50.0, 700.0)  # 700 m "lap" every 14 s
    out = resample_uniform({"TIME": t, "LAP_DIST": lap}, 50.0)
    d = out["LAP_DIST"]
    assert np.sum(np.diff(d) < -350) == 2          # two clean resets
    assert np.max(np.abs(np.diff(d)[np.diff(d) > -350])) < 2.0  # no fake mid-wrap values


def test_fill_gaps_holds_neighbours():
    a = np.array([np.nan, 1.0, np.nan, 3.0, np.nan])
    assert np.allclose(_fill_gaps(a), [1.0, 1.0, 2.0, 3.0, 3.0])
    assert np.all(_fill_gaps(np.array([np.nan, np.nan])) == 0)


def test_tc_estimate_uses_rear_slip():
    from vre_rpy.setup_calcs import compute_setup_channels

    n = 200
    dec = {
        "count": n, "time_s": np.arange(n) / 11.0, "throttle": np.full(n, 0.9), "brake": np.zeros(n),
        "speed_kmh": np.full(n, 100.0), "steer_deg": np.zeros(n), "g_lat": np.zeros(n), "g_lon": np.zeros(n),
        "slip_ratio": {"LF": np.zeros(n), "RF": np.zeros(n), "LR": np.r_[np.zeros(100), np.full(100, 0.12)],
                       "RR": np.zeros(n)},
    }
    ch = compute_setup_channels(dec)
    assert "VRE_TC_EST" in ch and "VRE_TC_ACT" not in ch
    assert ch["VRE_TC_EST"][:100].max() == 0 and ch["VRE_TC_EST"][100:].mean() > 0.9


def test_export_has_v2_channels(spa_pair_export):
    pytest.importorskip("ldparser")
    from ldparser import ldData

    d = ldData.fromfile(str(spa_pair_export / "motec" / "Main.ld"))
    names = [c.name for c in d.channs]
    for need in ("GPS_LATITUDE", "GPS_LONGITUDE", "LAP_DIST", "VRE_YAW_DEG", "VRE_BETA_DEG",
                 "VRE_UNDERSTEER_DEG", "VRE_EDGE_ALL4_L_M", "VRE_VALID", "VRE_ELEV_M", "G_VERT", "VRE_TC_EST"):
        assert need in names, need
    shorts = [c.short_name for c in d.channs]
    assert len(shorts) == len(set(shorts))
    beta = np.asarray(d["VRE_BETA_DEG"].data, float)
    assert np.percentile(np.abs(beta), 95) < 8.0
    yaw = np.asarray(d["VRE_YAW_DEG"].data, float)
    assert np.abs(yaw).max() > 5.0                   # was all zeros before v2
    from vre_rpy.verify_motec_export import check_ld_channels

    r = check_ld_channels(spa_pair_export / "motec" / "Main.ld")
    assert r["ok"] and not r["all_zero_vre"]
    assert r["lap_dist"]["resets"] >= 5
    assert abs(r["lap_dist"]["gps_vs_lapdist_ratio"] - 1.0) < 0.02
    wb = (spa_pair_export / "motec" / "VRE_Export" / "Workbooks" / "VRE_Compare.i2wkb").read_text()
    for sheet in ("VRE Corner", "VRE Terrain", "VRE Limits"):
        assert f'Name="{sheet}"' in wb
