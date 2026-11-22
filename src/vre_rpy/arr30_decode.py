"""Decode ACC replay input/physics array channels into named series."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .processor import ArrayBlob, extract_rpy

# Fallback only: per-car tyre radii come from the snapshot header (``tyre_radii``).
# MoTeC WHEEL_SPEED matches the header radii within ~0.8 % (296: 0.34 F / 0.3525 R);
# this single fitted value under-reads rear wheel speed by ~5 % on that car.
WHEEL_RADIUS_M = 0.337

CORNERS = ("LF", "RF", "LR", "RR")


def _arr_payload(blob: ArrayBlob) -> bytes:
    return blob.data


def find_arr(ex, stride: int, car_index: Optional[int] = None) -> Optional[ArrayBlob]:
    from .processor import snapshot_arrays

    arrays = snapshot_arrays(ex, car_index)
    if arrays is None:
        return None
    for a in arrays:
        if a.stride == stride and a.count > 0:
            return a
    return None


def tyre_radii(ex, car_index: Optional[int] = None) -> Optional[Dict[str, float]]:
    """Per-wheel tyre radius (m) from the 16-byte snapshot header: f32 [LF, RF, LR, RR]."""
    from .processor import snapshot_arrays

    arrays = snapshot_arrays(ex, car_index)
    if arrays is None:
        return None
    for a in arrays:
        if a.stride == 16 and a.count == 1 and len(a.data) >= 16:
            vals = np.frombuffer(a.data[:16], dtype="<f4").astype(np.float64)
            if np.all((vals > 0.2) & (vals < 0.6)):
                return {c: float(v) for c, v in zip(CORNERS, vals)}
    return None


def _f16(rec: np.ndarray, offset: int) -> np.ndarray:
    return rec[:, offset : offset + 2].view("<f2").ravel().astype(np.float64)


def _u16(rec: np.ndarray, offset: int) -> np.ndarray:
    return rec[:, offset : offset + 2].view("<u2").ravel().astype(np.float64)


def decode_arr30(
    arr30: ArrayBlob,
    arr48: Optional[ArrayBlob] = None,
    *,
    wheel_radius_m: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Decode all Motec-locked / code-backed lanes from arr_30 (+ arr_48)."""
    raw = _arr_payload(arr30)
    n = arr30.count
    if len(raw) < n * 0x30:
        raise ValueError(f"arr_30 payload short: {len(raw)} < {n * 0x30}")
    rec = np.frombuffer(raw[: n * 0x30], dtype=np.uint8).reshape(n, 0x30)

    throttle = rec[:, 4].astype(np.float64) / 255.0
    brake = rec[:, 5].astype(np.float64) / 255.0
    steer_deg = -_f16(rec, 2)
    speed_kmh = _u16(rec, 14) / 65535.0 * 500.0
    rpm = _f16(rec, 0)
    g_lat = _f16(rec, 24)
    g_lon = _f16(rec, 28)

    u108 = rec[:, 32:36].view("<u4").ravel()
    gear = np.maximum(((u108 >> 5) & 0xF).astype(np.int16) - 1, 0).astype(np.float64)

    # World velocity vector (m/s, Y up): |v| matches speed_kmh/3.6 at r > 0.99999.
    vel_x = _f16(rec, 16)
    vel_y = _f16(rec, 18)
    vel_z = _f16(rec, 20)
    # Vertical acceleration (g, deviation from 1 g; Y component of the G vector).
    g_vert = _f16(rec, 26)
    # Drivetrain-side speed (units unconfirmed; ~0.8x km/h under power) — hypothesis name.
    drivetrain_speed = _f16(rec, 22)
    flag_bit0 = (u108 & 1).astype(np.float64)
    flag_bits1_4 = ((u108 >> 1) & 0xF).astype(np.float64)

    radii = dict(wheel_radius_m) if wheel_radius_m else {c: WHEEL_RADIUS_M for c in CORNERS}
    wheel_angular = {_c: _f16(rec, off) for _c, off in zip(CORNERS, (6, 8, 10, 12))}
    wheel_speed_ms = {c: wheel_angular[c] * radii.get(c, WHEEL_RADIUS_M) for c in CORNERS}

    # Every input record carries its own session timestamp (f64 at +40); the per-wheel stream
    # (player car only) repeats it at +0x40. Other cars in race replays have only this one.
    time_s: Optional[np.ndarray] = rec[:, 40:48].copy().view("<f8").ravel().astype(np.float64)
    nd_slip: Dict[str, np.ndarray] = {}
    tyre_load: Dict[str, np.ndarray] = {}
    slip_ratio: Dict[str, np.ndarray] = {}
    marbles: Dict[str, np.ndarray] = {}
    is_on_marbles: Dict[str, np.ndarray] = {}
    phys_478: Dict[str, np.ndarray] = {}
    phys_500: Dict[str, np.ndarray] = {}
    wheel_dirt: Dict[str, np.ndarray] = {}
    disc_temp: Dict[str, np.ndarray] = {}

    if arr48 is not None and arr48.count == n:
        p48 = _arr_payload(arr48)
        if len(p48) >= n * 0x48:
            r48 = np.frombuffer(p48[: n * 0x48], dtype=np.uint8).reshape(n, 0x48)
            time_s = r48[:, 0x40:0x48].view("<f8").ravel().astype(np.float64)
            for c, off in zip(CORNERS, (0, 2, 4, 6)):
                nd_slip[c] = _f16(r48, off)
            for c, off in zip(CORNERS, (8, 10, 12, 14)):
                tyre_load[c] = _f16(r48, off)
            for c, off in zip(CORNERS, (16, 18, 20, 22)):
                slip_ratio[c] = _f16(r48, off)
            for c, off in zip(CORNERS, (26, 27, 28, 29)):
                marbles[c] = r48[:, off].astype(np.float64) / 255.0
            for c, off in zip(CORNERS, (30, 31, 32, 33)):
                is_on_marbles[c] = (r48[:, off] != 0).astype(np.float64)
            for c, off in zip(CORNERS, (34, 35, 36, 37)):
                phys_478[c] = r48[:, off].astype(np.float64) / 255.0
            for c, off in zip(CORNERS, (38, 40, 42, 44)):
                phys_500[c] = _f16(r48, off)
            for c, off in zip(CORNERS, (46, 47, 48, 49)):
                wheel_dirt[c] = r48[:, off].astype(np.float64) / 255.0
            for c, off in zip(CORNERS, (50, 52, 54, 56)):
                disc_temp[c] = _f16(r48, off)

    layout = {
        "throttle": "arr_30[+4] u8/255 ← physics+0x274",
        "brake": "arr_30[+5] u8/255 ← physics+0x278",
        "steer_deg": "arr_30[+2] -f16 ← physics+0x270",
        "gear": "arr_30[+32] ((u32>>5)&0xf)-1 ← physics+0x280&0xf",
        "speed_kmh": "arr_30[+14] u16*500/65535 ← physics+0x288*3.6",
        "rpm": "arr_30[+0] f16 ← physics+0x24c",
        "g_lat": "arr_30[+24] f16 ← physics+0x48c",
        "g_lon": "arr_30[+28] f16 ← physics+0x494",
        "wheel_angular": "arr_30[+6/+8/+10/+12] f16 ← physics+0x260..0x26c",
        "wheel_speed_ms": "wheel_angular * tyre radius (snapshot header; fallback %.3f m)" % WHEEL_RADIUS_M,
        "vel_xyz": "arr_30[+16/+18/+20] f16 world velocity m/s (Y up)",
        "g_vert": "arr_30[+26] f16 vertical g (deviation)",
        "drivetrain_speed": "arr_30[+22] f16 drivetrain-side speed (hypothesis name, units TBD)",
        "flags": "arr_30[+32] bit0, bits1-4 (meaning unknown)",
        "nd_slip": "arr_48[+0/+2/+4/+6] f16 ← physics+0x2c4+i*4 (FMOD nd_slip)",
        "tyre_load": "arr_48[+8/+10/+12/+14] f16 ← physics+0x2e4+i*4",
        "slip_ratio": "arr_48[+16/+18/+20/+22] f16 ← physics+0x2b4+i*4",
        "marbles": "arr_48[+26..+29] u8/255 ← physics+0x724 (FMOD marbles_level)",
        "is_on_marbles": "arr_48[+30..+33] u8 0|255 ← physics+0x8e8 (FMOD is_on_marbles)",
        "phys_478": "arr_48[+34..+37] u8/255 ← physics+0x478+i*4 (sus_proxy; Motec SUS |r_res|≈0.80)",
        "sus_proxy": "alias of phys_478 — normalized, not millimetres",
        "phys_500": "arr_48[+38/+40/+42/+44] f16 ← physics+0x500+i*4 (SUS-related)",
        "wheel_dirt": "arr_48[+46..+49] u8/255 ← physics+0x324+i*4",
        "disc_temp": "arr_48[+50/+52/+54/+56] f16 ← physics+0x6c0+i*4",
        "time_s": "arr_30[+40] f64 (= arr_48[+0x40] when present)",
        "packer": "internal",
        "not_in_packer": "tyre_press (state +0x5e4), tyre_tair (+0x5dc), SUS mm (+0x530), tc/abs InAction (+0x8f8/9)",
    }

    out: Dict[str, Any] = {
        "count": n,
        "throttle": throttle,
        "brake": brake,
        "steer_deg": steer_deg,
        "gear": gear,
        "speed_kmh": speed_kmh,
        "rpm": rpm,
        "g_lat": g_lat,
        "g_lon": g_lon,
        "vel_x": vel_x,
        "vel_y": vel_y,
        "vel_z": vel_z,
        "g_vert": g_vert,
        "drivetrain_speed": drivetrain_speed,
        "flag_bit0": flag_bit0,
        "flag_bits1_4": flag_bits1_4,
        "wheel_radius_m": radii,
        "wheel_angular": wheel_angular,
        "wheel_speed_ms": wheel_speed_ms,
        "nd_slip": nd_slip,
        "tyre_load": tyre_load,
        "slip_ratio": slip_ratio,
        "marbles": marbles,
        "is_on_marbles": is_on_marbles,
        "phys_478": phys_478,
        "sus_proxy": phys_478,
        "phys_500": phys_500,
        "wheel_dirt": wheel_dirt,
        "disc_temp": disc_temp,
        "time_s": time_s,
        "layout": layout,
    }
    return out


def decode_rpy_inputs(path, car_index: Optional[int] = None) -> Dict[str, Any]:
    ex = extract_rpy(path)
    a30 = find_arr(ex, 0x30, car_index)
    a48 = find_arr(ex, 0x48, car_index)
    if a30 is None:
        raise ValueError("no arr_30 in snapshot")
    return decode_arr30(a30, a48, wheel_radius_m=tyre_radii(ex, car_index))


def _take_arr(arr: Optional[np.ndarray], idx: np.ndarray) -> List[float]:
    if arr is None:
        return []
    return [float(x) for x in np.asarray(arr)[idx]]


def _take_corner(
    d: Dict[str, np.ndarray], idx: np.ndarray
) -> Dict[str, List[float]]:
    return {c: _take_arr(d[c], idx) for c in CORNERS if c in d}


def series_json(decoded: Dict[str, Any], *, max_samples: int = 0) -> Dict[str, Any]:
    n = int(decoded["count"])
    idx = np.arange(n)
    if max_samples and n > max_samples:
        idx = np.linspace(0, n - 1, max_samples).astype(int)

    return {
        "count": n,
        "samples": len(idx),
        "time_s": _take_arr(decoded.get("time_s"), idx),
        "throttle": _take_arr(decoded["throttle"], idx),
        "brake": _take_arr(decoded["brake"], idx),
        "steer_deg": _take_arr(decoded["steer_deg"], idx),
        "gear": _take_arr(decoded["gear"], idx),
        "speed_kmh": _take_arr(decoded["speed_kmh"], idx),
        "rpm": _take_arr(decoded["rpm"], idx),
        "g_lat": _take_arr(decoded["g_lat"], idx),
        "g_lon": _take_arr(decoded["g_lon"], idx),
        "vel_x": _take_arr(decoded.get("vel_x"), idx),
        "vel_y": _take_arr(decoded.get("vel_y"), idx),
        "vel_z": _take_arr(decoded.get("vel_z"), idx),
        "g_vert": _take_arr(decoded.get("g_vert"), idx),
        "wheel_angular": _take_corner(decoded.get("wheel_angular") or {}, idx),
        "wheel_speed_ms": _take_corner(decoded.get("wheel_speed_ms") or {}, idx),
        "nd_slip": _take_corner(decoded.get("nd_slip") or {}, idx),
        "tyre_load": _take_corner(decoded.get("tyre_load") or {}, idx),
        "slip_ratio": _take_corner(decoded.get("slip_ratio") or {}, idx),
        "marbles": _take_corner(decoded.get("marbles") or {}, idx),
        "is_on_marbles": _take_corner(decoded.get("is_on_marbles") or {}, idx),
        "phys_478": _take_corner(decoded.get("phys_478") or {}, idx),
        "sus_proxy": _take_corner(decoded.get("sus_proxy") or decoded.get("phys_478") or {}, idx),
        "phys_500": _take_corner(decoded.get("phys_500") or {}, idx),
        "wheel_dirt": _take_corner(decoded.get("wheel_dirt") or {}, idx),
        "disc_temp": _take_corner(decoded.get("disc_temp") or {}, idx),
        "layout": decoded["layout"],
    }


def flat_channel_map(decoded: Dict[str, Any]) -> Dict[str, np.ndarray]:
    """Flat name → array for Motec / CSV (Motec-style names where applicable)."""
    out: Dict[str, np.ndarray] = {
        "THROTTLE": np.asarray(decoded["throttle"]) * 100.0,
        "BRAKE": np.asarray(decoded["brake"]) * 100.0,
        "STEERANGLE": np.asarray(decoded["steer_deg"]),
        "GEAR": np.asarray(decoded["gear"]),
        "SPEED": np.asarray(decoded["speed_kmh"]) / 3.6,
        "SPEED_KMH": np.asarray(decoded["speed_kmh"]),
        "RPMS": np.asarray(decoded["rpm"]),
        "G_LAT": np.asarray(decoded["g_lat"]),
        "G_LON": np.asarray(decoded["g_lon"]),
    }
    for key, name in (("g_vert", "G_VERT"), ("vel_x", "VEL_X"), ("vel_y", "VEL_Y"), ("vel_z", "VEL_Z")):
        if decoded.get(key) is not None:
            out[name] = np.asarray(decoded[key])
    if decoded.get("time_s") is not None:
        out["TIME"] = np.asarray(decoded["time_s"])
    for c in CORNERS:
        if c in (decoded.get("wheel_speed_ms") or {}):
            out[f"WHEEL_SPEED_{c}"] = decoded["wheel_speed_ms"][c]
        if c in (decoded.get("wheel_angular") or {}):
            out[f"WHEEL_ANGULAR_{c}"] = decoded["wheel_angular"][c]
        if c in (decoded.get("disc_temp") or {}):
            out[f"BRAKE_TEMP_{c}"] = decoded["disc_temp"][c]
        if c in (decoded.get("nd_slip") or {}):
            out[f"ND_SLIP_{c}"] = decoded["nd_slip"][c]
        if c in (decoded.get("tyre_load") or {}):
            out[f"TYRE_LOAD_{c}"] = decoded["tyre_load"][c]
        if c in (decoded.get("slip_ratio") or {}):
            out[f"SLIP_RATIO_{c}"] = decoded["slip_ratio"][c]
        if c in (decoded.get("marbles") or {}):
            out[f"MARBLES_{c}"] = decoded["marbles"][c]
        if c in (decoded.get("is_on_marbles") or {}):
            out[f"IS_ON_MARBLES_{c}"] = decoded["is_on_marbles"][c]
        if c in (decoded.get("phys_478") or {}):
            out[f"PHYS_478_{c}"] = decoded["phys_478"][c]
            out[f"SUS_PROXY_{c}"] = decoded["phys_478"][c]
        if c in (decoded.get("phys_500") or {}):
            out[f"PHYS_500_{c}"] = decoded["phys_500"][c]
        if c in (decoded.get("wheel_dirt") or {}):
            out[f"WHEEL_DIRT_{c}"] = decoded["wheel_dirt"][c]
    return out
