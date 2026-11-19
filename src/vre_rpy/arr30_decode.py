"""Decode ACC .rpy arr_30 (0x30) + arr_48 (0x48) packed snapshot streams.

Source: FUN_140fdf850 @ 140fdf850 packs physics fields into counted arrays
that FUN_140fec540 / FUN_140fdcc00 serialize as opaque blobs.

arr_30 — car-level (Motec-locked where noted):
  [0:2]   f16 ← physics+0x24c          RPMS |r|≈0.81
  [2:4]   f16 ← physics+0x270          steer_deg = -f16; STEERANGLE |r|≈0.78
  [4]     u8  ← physics+0x274          throttle; |r|≈0.77
  [5]     u8  ← physics+0x278          brake; |r|≈0.86
  [6:8]   f16 ← physics+0x260          wheel_angular LF; WHEEL_SPEED |r|≈0.98
  [8:10]  f16 ← physics+0x264          wheel_angular RF
  [10:12] f16 ← physics+0x268          wheel_angular LR
  [12:14] f16 ← physics+0x26c          wheel_angular RR
  [14:16] u16 ← physics+0x288*3.6      speed_kmh; |r|≈0.99
  [24:26] f16 ← physics+0x48c low      g_lat; |r|≈0.93
  [28:30] f16 ← physics+0x494          g_lon; |r|≈0.85
  [32:36] u32 flags                    gear = ((u32>>5)&0xf)-1; |r|≈0.96

arr_48 — per-wheel loop (LF/RF/LR/RR) + time:
  [0:8]   f16×4  ← nd_slip @ physics+0x2c4+i*4   (FMOD nd_slip; |r|≈0.64 vs |slip|)
  [8:16]  f16×4  ← tyre_load @ physics+0x2e4+i*4
  [16:24] f16×4  ← slip_ratio @ physics+0x2b4+i*4
  [26:30] u8×4   ← marbles @ physics+0x724+i*0x1c  (/255; FMOD marbles_level)
  [30:34] u8×4   ← is_on_marbles @ physics+0x8e8+i*4  (0|255; FMOD is_on_marbles)
  [34:38] u8×4   ← physics+0x478+i*4  (/255; Motec SUS_TRAVEL |r_res|≈0.80 → sus_proxy)
  [38:46] f16×4  ← physics+0x500+i*4  (SUS-related |r|~0.6; Motec name TBD)
  [46:50] u8×4   ← wheel_dirt @ physics+0x324+i*4  (/255)
  [50:58] f16×4  ← disc_temp @ physics+0x6c0+i*4   BRAKE_TEMP |r|≈0.97
  [0x40:0x48] f64 timestamp

Still NO-GO in this packer as Motec-native units: tyre_press, tyre_tair,
SUS_TRAVEL mm (sus_proxy is normalized 0-1 only), TC/ABS InAction.
Code-backed physics offsets (FUN_14347b010 state filler; NOT file-packed):
  tyre_press physics+0x5e4+i*0x3c, tyre_tair +0x5dc+i*0x3c, sus_travel(m)
  +0x530+i*4, TC InAction u8 +0x8f8, ABS InAction u8 +0x8f9.
  (physics+0x5ac is the FMOD inflation-loss term, 1-x; not PSI.)

Wheel linear speed (Motec m/s): angular_f16 * WHEEL_RADIUS_M (fitted ≈0.337).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .processor import ArrayBlob, extract_rpy

# Fitted: Motec WHEEL_SPEED_m/s ≈ f16(angular) * radius
WHEEL_RADIUS_M = 0.337

CORNERS = ("LF", "RF", "LR", "RR")


def _arr_payload(blob: ArrayBlob) -> bytes:
    return blob.data


def find_arr(ex, stride: int, car_index: int = 0) -> Optional[ArrayBlob]:
    if not ex.car_records or car_index >= len(ex.car_records):
        return None
    snaps = ex.car_records[car_index].snapshots
    if not snaps:
        return None
    for a in snaps[0].arrays:
        if a.stride == stride and a.count > 0:
            return a
    return None


def _f16(rec: np.ndarray, offset: int) -> np.ndarray:
    return rec[:, offset : offset + 2].view("<f2").ravel().astype(np.float64)


def _u16(rec: np.ndarray, offset: int) -> np.ndarray:
    return rec[:, offset : offset + 2].view("<u2").ravel().astype(np.float64)


def decode_arr30(
    arr30: ArrayBlob,
    arr48: Optional[ArrayBlob] = None,
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

    wheel_angular = {_c: _f16(rec, off) for _c, off in zip(CORNERS, (6, 8, 10, 12))}
    wheel_speed_ms = {c: wheel_angular[c] * WHEEL_RADIUS_M for c in CORNERS}

    time_s: Optional[np.ndarray] = None
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
        "wheel_speed_ms": f"wheel_angular * {WHEEL_RADIUS_M} (Motec-fitted radius)",
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
        "time_s": "arr_48[+0x40] f64",
        "packer": "FUN_140fdf850",
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


def decode_rpy_inputs(path, car_index: int = 0) -> Dict[str, Any]:
    ex = extract_rpy(path)
    a30 = find_arr(ex, 0x30, car_index)
    a48 = find_arr(ex, 0x48, car_index)
    if a30 is None:
        raise ValueError("no arr_30 in snapshot")
    return decode_arr30(a30, a48)


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
