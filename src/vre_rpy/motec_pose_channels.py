"""Pose-derived MoTeC channels on the input clock (any car, any track).

Pose frames (car position + 4 wheel points) are timed by ``frame_clock``; here every pose quantity is
interpolated onto the input samples' timestamps so it can sit in the same ``.ld`` as SPEED/THROTTLE.
Channels are omitted (not zero-filled) when their source is unavailable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

EARTH_R = 6_371_000.0
GPS_ORIGIN = (0.0, 0.0)  # synthetic local tangent plane; only relative geometry matters to MoTeC


def _interp_angle_deg(tq, t, a_deg):
    un = np.unwrap(np.radians(a_deg))
    return np.degrees(np.interp(tq, t, un))


def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def pose_channels_for_inputs(rf, *, steering_ratio: float = 11.0) -> Dict[str, Any]:
    """Return ``{"channels": {name: array aligned to inputs['time_s']}, "units": {...}, "meta": {...}}``."""
    p, inp = rf.pose, rf.inputs
    it = np.asarray(inp["time_s"], dtype=np.float64)
    t = np.asarray(rf.t, dtype=np.float64)
    order = np.argsort(t)
    t = t[order]
    keep = np.r_[True, np.diff(t) > 1e-6]
    t, order = t[keep], order[keep]
    inside = (it >= t[0]) & (it <= t[-1])

    ch: Dict[str, np.ndarray] = {}
    units: Dict[str, str] = {}

    def put(name, arr, unit):
        a = np.asarray(arr, dtype=np.float64)
        a = np.where(inside, a, np.nan)
        ch[name] = a
        units[name] = unit

    x = np.interp(it, t, p["x"][order])
    y = np.interp(it, t, p["y"][order])
    z = np.interp(it, t, p["z"][order])
    # Screen convention: X east, -Z north (keeps clockwise tracks clockwise in MoTeC's map).
    lat = GPS_ORIGIN[0] + np.degrees(-z / EARTH_R)
    lon = GPS_ORIGIN[1] + np.degrees(x / (EARTH_R * np.cos(np.radians(GPS_ORIGIN[0]))))
    put("GPS_LATITUDE", lat, "deg")
    put("GPS_LONGITUDE", lon, "deg")
    put("GPS_ALTITUDE", y, "m")
    put("VRE_ELEV_M", y, "m")

    yaw = _interp_angle_deg(it, t, p["yaw_deg"][order])
    yaw_rate = np.gradient(np.radians(yaw), it)
    put("VRE_YAW_RATE", np.degrees(yaw_rate), "deg/s")
    vx, vz = np.asarray(inp.get("vel_x")), np.asarray(inp.get("vel_z"))
    if inp.get("vel_x") is not None:
        course = np.degrees(np.arctan2(vx, vz))
        moving = np.sqrt(vx**2 + vz**2) > 5.0
        put("VRE_BETA_DEG", np.where(moving, _wrap180(yaw - course), 0.0), "deg")
        v = np.sqrt(vx**2 + vz**2)
        wb = _wheelbase(p)
        if wb:
            delta = np.radians(np.asarray(inp["steer_deg"]) / steering_ratio)
            # understeer angle: steered angle minus kinematic (Ackermann) angle for the yaw rate
            # steer and yaw rate share a sign (right turn: both negative); + = understeer
            kin = yaw_rate * wb / np.maximum(v, 1e-3)
            ua = np.where(v > 8.0, np.sign(delta) * (delta - kin), np.nan)
            put("VRE_UNDERSTEER_DEG", np.degrees(ua), "deg")

    meta: Dict[str, Any] = {"steering_ratio_assumed": steering_ratio, "sync": rf.sync}
    if rf.line is not None:
        L = rf.line.length_m
        xz_pose = np.c_[p["x"][order], p["z"][order]]
        pr = rf.line.project(xz_pose)
        s_un = np.unwrap(pr["s_m"] / L * 2 * np.pi) / (2 * np.pi) * L
        s_i = np.interp(it, t, s_un)
        # lap distance from the S/F line: offset = line distance at the timer's crossings
        sf = np.array([l.get("sf_time_s") for l in rf.laps[1:] if l.get("sf_time_s") is not None])
        sf = sf[(sf > t[0]) & (sf < t[-1])]
        s_sf = float(np.median(np.mod(np.interp(sf, t, s_un), L))) if len(sf) else 0.0
        put("LAP_DIST", np.mod(s_i - s_sf, L), "m")
        put("VRE_LAT_OFFSET_M", np.interp(it, t, pr["lat_m"]), "m")
        bl, br = [], []
        for w in range(4):
            b = rf.line.beyond_edge(p["wheels"][order, w][:, [0, 2]])
            bl.append(b["left"])
            br.append(b["right"])
        bl, br = np.array(bl), np.array(br)
        put("VRE_EDGE_ALL4_L_M", np.interp(it, t, bl.min(0)), "m")
        put("VRE_EDGE_ALL4_R_M", np.interp(it, t, br.min(0)), "m")
        put("VRE_EDGE_ANY_L_M", np.interp(it, t, bl.max(0)), "m")
        put("VRE_EDGE_ANY_R_M", np.interp(it, t, br.max(0)), "m")
        dy = np.gradient(y)
        ds = np.maximum(np.gradient(s_i), 0.05)
        grade = dy / ds
        put("VRE_GRADE_PCT", 100 * _smooth(grade, 9), "%")
        put("VRE_VERT_CURV", 1000 * _smooth(np.gradient(_smooth(grade, 9)) / ds, 9), "1/km")
        meta.update({"track_length_m": L, "sf_offset_m": s_sf})
        corners = (rf.track_config or {}).get("corners") or []
        if corners:
            cid = np.zeros(len(it))
            lapd = np.mod(s_i - s_sf, L) / L
            for k, c in enumerate(sorted(corners, key=lambda c: c.get("spline_start", 0)), start=1):
                cid[(lapd >= c.get("spline_start", 0)) & (lapd < c.get("spline_end", 0))] = k
            put("VRE_CORNER_ID", cid, "id")
            meta["corner_ids"] = {
                k: (c.get("display_name") or c.get("name"))
                for k, c in enumerate(sorted(corners, key=lambda c: c.get("spline_start", 0)), start=1)
            }

    loads = inp.get("tyre_load") or {}
    if loads:
        put("VRE_LOAD_TOTAL", sum(np.asarray(v, float) for v in loads.values()), "N")

    # lap validity and ACC's predicted lap, step-held from the lap timer
    if rf.laps:
        valid = np.ones(len(it))
        for l in rf.laps:
            ia = l.get("invalid_at_session_s")
            if ia is None:
                continue
            end = l.get("session_end_s") or ia
            valid[(it >= ia) & (it <= end)] = 0.0
        put("VRE_VALID", valid, "flag")
    if rf.predicted is not None:
        pt, pm = rf.predicted["time_s"], rf.predicted["predicted_ms"]
        k = np.clip(np.searchsorted(pt, it, side="right") - 1, 0, len(pt) - 1)
        pred_s = pm[k] / 1000.0
        pred_s = np.where((pred_s > 20.0) & (pred_s < 1800.0), pred_s, np.nan)
        put("VRE_PRED_LAP_S", np.nan_to_num(pred_s, nan=0.0), "s")
        best = np.full(len(it), np.nan)
        b = np.inf
        done = sorted(
            (l["session_end_s"], l["lap_ms"] / 1000.0)
            for l in rf.laps
            if l.get("completed") and l.get("valid") and l.get("flying_candidate")
        )
        j = 0
        for i_, tt in enumerate(it):
            while j < len(done) and done[j][0] <= tt:
                b = min(b, done[j][1])
                j += 1
            best[i_] = b if np.isfinite(b) else np.nan
        delta = pred_s - best
        put("VRE_PRED_DELTA_S", np.where(np.abs(delta) < 60.0, delta, 0.0), "s")
    put("VRE_SYNC_OK", np.full(len(it), 1.0 if rf.sync.get("sync_go") else 0.0), "flag")
    return {"channels": ch, "units": units, "meta": meta}


def _smooth(a, k):
    a = np.nan_to_num(np.asarray(a, dtype=np.float64))
    if k <= 1:
        return a
    return np.convolve(a, np.ones(k) / k, mode="same")


def _wheelbase(p) -> Optional[float]:
    w = p.get("wheels")
    if w is None:
        return None
    f = w[:, 0:2, :].mean(axis=1)
    r = w[:, 2:4, :].mean(axis=1)
    return float(np.median(np.linalg.norm(f - r, axis=1)))
