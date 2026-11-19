"""VRE setup-tuning calc channels from GO .rpy streams.

Derived from Motec worksheet practice (wheelspeed, US/OS, yaw, coast/gap) using
only file_go lanes.

Electronics are two dimensions (see `electronics.py`):
  - **levels** (TC=7, ABS=2) → `VRE_TC_LEVEL` / `VRE_ABS_LEVEL` when supplied
  - **active** (fires on corner) → `VRE_TC_ACT` / `VRE_ABS_ACT` proxies until
    HUD `TC_InAction` / `ABS_InAction` packer lanes Motec-lock
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import numpy as np

from vre_rpy.analysis_channels import (
    coast_mask,
    pedal_gap_s,
    tc_active,
    us_os,
    wheelspin_lock,
    yaw_rate_deg_s,
)
from vre_rpy.electronics import (
    electronics_notes,
    level_channels,
    levels_from_mapping,
)


def _as01(pedal: np.ndarray) -> np.ndarray:
    """Accept 0..1 or 0..100 pedals."""
    p = np.asarray(pedal, dtype=np.float64)
    if p.size and np.nanmax(np.abs(p[: min(500, len(p))])) > 1.5:
        return np.clip(p / 100.0, 0.0, 1.0)
    return np.clip(p, 0.0, 1.0)


def compute_setup_channels(
    decoded: Dict[str, Any],
    *,
    track_grip: Optional[float] = None,
    steering_lock_deg: float = 400.0,
    electronics: Optional[Mapping[str, Any]] = None,
) -> Dict[str, np.ndarray]:
    """Return Motec-ready float32 series keyed by channel name."""
    n = int(decoded["count"])
    lv = levels_from_mapping(electronics)
    thr = _as01(decoded["throttle"])
    brk = _as01(decoded["brake"])
    spd = np.asarray(decoded["speed_kmh"], dtype=np.float64)
    steer = np.asarray(decoded["steer_deg"], dtype=np.float64)
    g_lat = np.asarray(decoded["g_lat"], dtype=np.float64)
    g_lon = np.asarray(decoded["g_lon"], dtype=np.float64)

    time_s = decoded.get("time_s")
    if time_s is not None and len(time_s) > 1:
        hz = float(1.0 / max(np.median(np.diff(np.asarray(time_s, dtype=np.float64))), 1e-3))
        hz = float(np.clip(hz, 1.0, 200.0))
    else:
        hz = 50.0

    ws = decoded.get("wheel_speed_ms") or {}
    wlf = np.asarray(ws.get("LF", np.zeros(n)), dtype=np.float64)
    wrf = np.asarray(ws.get("RF", np.zeros(n)), dtype=np.float64)
    wlr = np.asarray(ws.get("LR", np.zeros(n)), dtype=np.float64)
    wrr = np.asarray(ws.get("RR", np.zeros(n)), dtype=np.float64)

    spin, lock = wheelspin_lock(spd, wlf, wrf, wlr, wrr, spin_thresh_kmh=5.0, lock_thresh_kmh=5.0)
    # Stricter ABS proxy: brake on + front lock (Motec ABS recall ~0.8 @ 5km/h)
    abs_proxy = (
        ((spd - np.minimum(wlf, wrf) * 3.6) > 5.0) & (brk > 0.05)
    ).astype(np.float64)
    # TC proxy: throttle on + rear spin
    tc_proxy = (
        ((np.maximum(wlr, wrr) * 3.6 - spd) > 3.0) & (thr > 0.15)
    ).astype(np.float64)

    # Yaw from heading rate if present, else from steer/G heuristic via us_os sibling
    heading = decoded.get("heading_deg")
    if heading is not None and time_s is not None and len(heading) == n:
        h = np.asarray(heading, dtype=np.float64)
        # unwrap-ish via gradient in deg/s → rad/s for yaw_rate_deg_s helper
        dh = np.gradient(h, np.asarray(time_s, dtype=np.float64))
        yaw_deg = yaw_rate_deg_s(np.radians(dh))
    else:
        yaw_deg = [0.0] * n

    balance = us_os(spd, steer, g_lat, steering_lock_deg=steering_lock_deg)

    # Platform: prefer sus_proxy (phys+0x478; Motec SUS |r_res|≈0.80) for rake;
    # fall back to tyre_load F−R.
    sus = decoded.get("sus_proxy") or decoded.get("phys_478") or {}
    loads = decoded.get("tyre_load") or {}
    if all(c in sus for c in ("LF", "RF", "LR", "RR")):
        f = 0.5 * (sus["LF"] + sus["RF"])
        r = 0.5 * (sus["LR"] + sus["RR"])
        rake = np.asarray(f - r, dtype=np.float64)
        load_front = np.asarray(f, dtype=np.float64)
        load_rear = np.asarray(r, dtype=np.float64)
        sus_channels = {f"VRE_SUS_PROXY_{c}": np.asarray(sus[c], dtype=np.float32) for c in ("LF", "RF", "LR", "RR")}
    elif all(c in loads for c in ("LF", "RF", "LR", "RR")):
        f = 0.5 * (loads["LF"] + loads["RF"])
        r = 0.5 * (loads["LR"] + loads["RR"])
        rake = np.asarray(f - r, dtype=np.float64)
        load_front = np.asarray(f, dtype=np.float64)
        load_rear = np.asarray(r, dtype=np.float64)
        sus_channels = {}
    else:
        rake = np.zeros(n)
        load_front = np.zeros(n)
        load_rear = np.zeros(n)
        sus_channels = {}

    # Slip aggregates
    slips = decoded.get("slip_ratio") or {}
    if slips:
        slip_max = np.maximum.reduce([np.abs(slips[c]) for c in slips])
        slip_rear = np.maximum(np.abs(slips.get("LR", 0)), np.abs(slips.get("RR", 0)))
        slip_front = np.maximum(np.abs(slips.get("LF", 0)), np.abs(slips.get("RF", 0)))
    else:
        slip_max = slip_rear = slip_front = np.zeros(n)

    # Brake temp balance
    temps = decoded.get("disc_temp") or {}
    if all(c in temps for c in ("LF", "RF", "LR", "RR")):
        bt_f = 0.5 * (temps["LF"] + temps["RF"])
        bt_r = 0.5 * (temps["LR"] + temps["RR"])
        bt_bal = np.asarray(bt_f - bt_r, dtype=np.float64)
    else:
        bt_bal = np.zeros(n)

    grip = float(track_grip) if track_grip is not None else 0.0
    grip_series = np.full(n, grip, dtype=np.float64)

    # Combined lateral demand vs grip (g_lat magnitude / grip) — setup AI hint
    if grip > 0.05:
        lat_util = np.abs(g_lat) / (grip * 9.81)  # rough: grip_norm * g
    else:
        lat_util = np.abs(g_lat)

    # Advanced analytics from GO streams (auto Motec channels).
    combined_g = np.sqrt(g_lat * g_lat + g_lon * g_lon)
    trail_brake = ((brk > 0.05) & (thr > 0.05)).astype(np.float64)
    brk_rate = np.gradient(brk, 1.0 / hz) if n > 1 else np.zeros(n)
    thr_rate = np.gradient(thr, 1.0 / hz) if n > 1 else np.zeros(n)
    # Distance along session from speed (m); Motec also integrates internally.
    spd_ms = spd / 3.6
    dist_m = np.cumsum(spd_ms / max(hz, 1.0)) if n > 1 else np.zeros(n)
    # Rolling brake pressure while decelerating (trail / release quality)
    release = np.clip(-brk_rate, 0.0, None)

    out: Dict[str, np.ndarray] = {
        "VRE_COAST": np.asarray(coast_mask(thr.tolist(), brk.tolist(), spd.tolist()), dtype=np.float32),
        "VRE_PEDAL_GAP": np.asarray(pedal_gap_s(thr.tolist(), brk.tolist(), hz), dtype=np.float32),
        "VRE_STEER_RATE": np.asarray(
            np.gradient(steer, 1.0 / hz) if n > 1 else np.zeros(n), dtype=np.float32
        ),
        "VRE_SPEED_KMH": spd.astype(np.float32),
        "VRE_US_OS": np.asarray(balance, dtype=np.float32),
        "VRE_YAW_DEG": np.asarray(yaw_deg, dtype=np.float32),
        "VRE_SPIN": np.asarray(spin, dtype=np.float32),
        "VRE_LOCK": np.asarray(lock, dtype=np.float32),
        "VRE_TC_ACT": np.asarray(tc_active(tc_proxy), dtype=np.float32),
        "VRE_ABS_ACT": np.asarray(tc_active(abs_proxy), dtype=np.float32),
        "VRE_RAKE_LOAD": rake.astype(np.float32),
        "VRE_LOAD_F": load_front.astype(np.float32),
        "VRE_LOAD_R": load_rear.astype(np.float32),
        **sus_channels,
        "VRE_SLIP_MAX": np.asarray(slip_max, dtype=np.float32),
        "VRE_SLIP_F": np.asarray(slip_front, dtype=np.float32),
        "VRE_SLIP_R": np.asarray(slip_rear, dtype=np.float32),
        "VRE_BT_BAL": bt_bal.astype(np.float32),
        "VRE_TRACK_GRIP": grip_series.astype(np.float32),
        "VRE_LAT_UTIL": np.asarray(lat_util, dtype=np.float32),
        "VRE_G_LAT": g_lat.astype(np.float32),
        "VRE_G_LON": g_lon.astype(np.float32),
        "VRE_COMBINED_G": combined_g.astype(np.float32),
        "VRE_TRAIL_BRAKE": trail_brake.astype(np.float32),
        "VRE_BRAKE_RATE": np.asarray(brk_rate, dtype=np.float32),
        "VRE_THROTTLE_RATE": np.asarray(thr_rate, dtype=np.float32),
        "VRE_BRAKE_RELEASE": np.asarray(release, dtype=np.float32),
        "VRE_DIST_M": dist_m.astype(np.float32),
    }
    out.update(
        level_channels(
            n,
            tc1=lv["tc1"],
            tc2=lv["tc2"],
            abs_level=lv["abs"],
            engine_map=lv["engine_map"],
        )
    )
    return out


def channel_notes() -> Dict[str, str]:
    notes = {
        "VRE_TC_ACT": "STATE proxy → Motec Wheelspeed/TC; raise TC maps only if ACT costs pace",
        "VRE_ABS_ACT": "STATE proxy → Motec Wheelspeed/ABS; raise ABS map only if lock costs entry time",
        "VRE_TRACK_GRIP": "env → scale G expectations; weatherData[6] raw (name hyp)",
        "VRE_US_OS": "Motec Understeer/Oversteer worksheet; multi-corner → springs/ARB/aero not calm TC",
        "VRE_RAKE_LOAD": "F−R from sus_proxy (phys+0x478) when present, else tyre_load",
        "VRE_SUS_PROXY_LF": "arr_48 phys+0x478; Motec SUS_TRAVEL |r_res|≈0.80 (normalized 0-1, not mm)",
        "VRE_SUS_PROXY_RF": "arr_48 phys+0x478; Motec SUS_TRAVEL |r_res|≈0.80 (normalized 0-1, not mm)",
        "VRE_SUS_PROXY_LR": "arr_48 phys+0x478; Motec SUS_TRAVEL |r_res|≈0.80 (normalized 0-1, not mm)",
        "VRE_SUS_PROXY_RR": "arr_48 phys+0x478; Motec SUS_TRAVEL |r_res|≈0.80 (normalized 0-1, not mm)",
        "VRE_COAST": "Driver worksheet; Sonoma: coast B+C ≠ soft the car",
        "VRE_PEDAL_GAP": "Driver worksheet; fix technique before setup",
        "VRE_SPIN": "Wheelspeed; pair with VRE_TC_ACT / diff exit",
        "VRE_LOCK": "Wheelspeed; pair with VRE_ABS_ACT / bias",
        "VRE_BT_BAL": "Brake temp F−R; ducts/bias after tyre window",
        "VRE_LAT_UTIL": "|G_LAT| vs track grip — corner ranking",
        "VRE_COMBINED_G": "sqrt(G_LAT^2+G_LON^2) — total tyre demand",
        "VRE_TRAIL_BRAKE": "1 when brake+throttle overlap (technique / trail)",
        "VRE_BRAKE_RATE": "d(brake)/dt — onset vs release sharpness",
        "VRE_THROTTLE_RATE": "d(throttle)/dt — pickup aggressiveness",
        "VRE_BRAKE_RELEASE": "max(0, -brake_rate) — release smoothness",
        "VRE_DIST_M": "session distance from SPEED integral (m)",
    }
    notes.update(electronics_notes())
    return notes
