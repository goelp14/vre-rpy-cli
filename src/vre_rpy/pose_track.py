"""Pose + track-relative channels from .rpy rec_68.

Proven layout (Spa Tomato Motec/path validated):
  f32[0:3]  world position X,Y,Z (Y = elevation)
  f32[23]   normalized spline 0..1 (wraps each lap); path-speed |r|≈0.98
  heading   derived from horizontal motion (no reliable quat in record yet)

Lap validity (isValidLap analogue): spline-wrap segments are matched in order
to lap-timer laps (``lap_validity``: arr24 +8 u8) and get ``valid_flag`` /
``invalid_at_lap_ms``; unmatched segments keep ``valid_flag=None``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .processor import ArrayBlob, extract_rpy

_PACK = Path(__file__).resolve().parent / "track_pack"
# Legacy location when vendored inside assetto-corsa-mechanic (external/query_tools/acc_rpy).
_TRACK_CFG = Path(__file__).resolve().parents[3] / "ac_app" / "config" / "tracks"


def find_pose(ex, car_index: Optional[int] = None) -> Optional[ArrayBlob]:
    from .processor import snapshot_arrays

    arrays = snapshot_arrays(ex, car_index)
    if arrays is None:
        return None
    for a in arrays:
        if a.stride == 0x68 and a.count > 0:
            return a
    return None


def load_track_config(track_id: str) -> Dict[str, Any]:
    key = track_id.strip().lower()
    base = key[:-4] if key.endswith("_acc") else key
    # Prefer the pack generated from the track's AI line (+ notes / curated names); fall back to
    # hand-made configs for tracks without one.
    from .track_pack_gen import load_track_pack

    gen = load_track_pack(base)
    if gen is not None:
        gen.setdefault("display_name", gen.get("display_name") or base)
        return gen
    candidates = []
    for d in (_PACK, _TRACK_CFG):
        candidates += [d / f"{base}_acc.json", d / f"{key}.json", d / f"{base}.json"]
    for c in candidates:
        if c.is_file():
            return json.loads(c.read_text(encoding="utf-8"))
    return {
        "track_id": key,
        "length_m": 7000.0,
        "corners": [],
        "sectors": [],
    }


def decode_pose_track(
    pose: ArrayBlob,
    *,
    track_length_m: float = 7004.0,
    corners: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    n = pose.count
    rec = np.frombuffer(pose.data[: n * 0x68], dtype=np.float32).reshape(n, 26)
    # Wheel contact points, world XYZ: front +side, front -side, rear +side, rear -side.
    wheels = np.stack([rec[:, a : a + 3] for a in (5, 8, 11, 14)], axis=1).astype(np.float64)
    fwd = wheels[:, 0:2, :].mean(axis=1) - wheels[:, 2:4, :].mean(axis=1)
    # Body yaw from the wheel axis (independent of motion; differs from course by the slip angle).
    yaw_deg = np.degrees(np.arctan2(fwd[:, 0], fwd[:, 2]))
    x = rec[:, 0].astype(np.float64)
    y = rec[:, 1].astype(np.float64)
    z = rec[:, 2].astype(np.float64)
    spline = rec[:, 23].astype(np.float64)

    # Motion heading (deg) on XZ plane
    dx = np.diff(x, prepend=x[0])
    dz = np.diff(z, prepend=z[0])
    heading_deg = np.degrees(np.arctan2(dx, dz))
    speed_path_ms = np.sqrt(dx * dx + dz * dz)  # per-sample Δs (not /dt)

    wraps = list(np.where((spline[:-1] > 0.9) & (spline[1:] < 0.1))[0])
    lap_segments: List[Dict[str, Any]] = []
    for i, a in enumerate(wraps):
        b = wraps[i + 1] if i + 1 < len(wraps) else None
        if b is None:
            break
        n_samp = int(b - a)
        # Reject garage soaks (very long) and tiny blips
        if n_samp < 500 or n_samp > 20000:
            continue
        lap_segments.append(
            {
                "i0": int(a + 1),
                "i1": int(b),
                "n_samples": n_samp,
                "spline_wrap_index": i,
                "valid_flag": None,  # filled by lap_validity.attach_validity_to_segments
                "status": "spline_complete_candidate",
            }
        )

    corner_id = np.full(n, "", dtype=object)
    if corners:
        for c in corners:
            lo = float(c.get("spline_start", 0))
            hi = float(c.get("spline_end", 1))
            name = str(c.get("display_name") or c.get("name") or "")
            mask = (spline >= lo) & (spline < hi)
            corner_id[mask] = name

    dist_m = spline * float(track_length_m)

    return {
        "count": n,
        "x": x,
        "y": y,
        "z": z,
        "spline": spline,
        "dist_m": dist_m,
        "heading_deg": heading_deg,
        "yaw_deg": yaw_deg,
        "wheels": wheels,
        "lap_segments": lap_segments,
        "corner_id": corner_id,
        "layout": {
            "position": "rec_68 f32[0:3] world XYZ (Y up)",
            "spline": "rec_68 f32[23] normalized 0..1 (lap wraps)",
            "heading_deg": "derived atan2(dx,dz) from pose deltas (course)",
            "yaw_deg": "body yaw from wheel axis rec_68 f32[5:17] (front mid - rear mid)",
            "wheels": "rec_68 f32[5:17] wheel contact XYZ: front +side, front -side, rear +side, rear -side",
            "dist_m": f"spline * track_length_m ({track_length_m})",
            "valid_lap": "file_go_empirical — arr24[+8] u8 via lap_validity (segments matched by lap duration)",
        },
    }


def decode_rpy_pose_track(
    path,
    *,
    track_id: Optional[str] = None,
    car_index: Optional[int] = None,
) -> Dict[str, Any]:
    from .parse_rpy import parse_rpy_metadata

    path = Path(path)
    meta = parse_rpy_metadata(path)
    tid = track_id or (meta.get("filename") or {}).get("track") or "unknown"
    cfg = load_track_config(str(tid))
    ex = extract_rpy(path)
    pose = find_pose(ex, car_index)
    if pose is None:
        raise ValueError("no pose rec_68 in snapshot")
    decoded = decode_pose_track(
        pose,
        track_length_m=float(cfg.get("length_m") or 7004),
        corners=cfg.get("corners"),
    )
    decoded["track_id"] = cfg.get("track_id") or tid
    try:
        from .lap_validity import (
            attach_validity_to_segments,
            decode_lap_validity,
            find_lap_timer_array,
        )

        arr24 = find_lap_timer_array(ex, car_index)
        if arr24 is not None:
            from .frame_clock import fit_pose_clock

            timer_laps = decode_lap_validity(arr24.data)
            decoded["timer_laps"] = timer_laps
            span = None
            from .processor import player_car_index, snapshot_arrays

            # clock span from the player's per-wheel stream, or this car's own input timestamps
            a48 = next((a for a in (snapshot_arrays(ex, player_car_index(ex)) or []) if a.stride == 0x48 and a.count), None)
            if a48 is not None:
                tt = np.frombuffer(a48.data[: a48.count * 0x48], dtype=np.uint8).reshape(a48.count, 0x48)
                tt = tt[:, 0x40:0x48].copy().view("<f8").ravel()
                span = (float(tt[0]), float(tt[-1]))
            pose_time = None
            try:
                clock = fit_pose_clock(decoded["spline"], timer_laps, span_s=span)
                pose_time = clock["t"]
                decoded["time_s"] = pose_time
                decoded["clock"] = {k: v for k, v in clock.items() if k != "t"}
            except ValueError as exc:
                decoded["clock_error"] = str(exc)
            decoded["lap_segments"] = attach_validity_to_segments(
                decoded["lap_segments"], timer_laps, pose_time_s=pose_time
            )
    except Exception as exc:  # validity is additive; never break pose export
        decoded["timer_laps_error"] = str(exc)
    decoded["track_config"] = {
        "track_id": cfg.get("track_id"),
        "display_name": cfg.get("display_name"),
        "length_m": cfg.get("length_m"),
        "corners": cfg.get("corners"),
        "sectors": cfg.get("sectors"),
        "direction": cfg.get("direction"),
    }
    return decoded


def series_json(
    decoded: Dict[str, Any],
    *,
    max_samples: int = 8000,
) -> Dict[str, Any]:
    n = int(decoded["count"])
    idx = np.arange(n)
    if max_samples and n > max_samples:
        idx = np.linspace(0, n - 1, max_samples).astype(int)

    def take(key: str) -> List[float]:
        return [float(x) for x in np.asarray(decoded[key])[idx]]

    return {
        "count": n,
        "samples": len(idx),
        "track_id": decoded.get("track_id"),
        "x": take("x"),
        "y": take("y"),
        "z": take("z"),
        "spline": take("spline"),
        "dist_m": take("dist_m"),
        "heading_deg": take("heading_deg"),
        "yaw_deg": take("yaw_deg") if "yaw_deg" in decoded else [],
        "time_s": take("time_s") if decoded.get("time_s") is not None else [],
        "clock": decoded.get("clock"),
        "corner_id": [str(decoded["corner_id"][i]) for i in idx],
        "lap_segments": decoded.get("lap_segments"),
        "layout": decoded.get("layout"),
        "track_config": decoded.get("track_config"),
    }


def best_segment_index(decoded: Dict[str, Any]) -> Optional[int]:
    """Index into ``lap_segments`` of the fastest valid completed lap (None if unknown)."""
    segs = decoded.get("lap_segments") or []
    good = [
        (s["lap_ms"], i)
        for i, s in enumerate(segs)
        if s.get("valid_flag") is True and s.get("lap_ms") and s.get("completed", True)
    ]
    return min(good)[1] if good else None


def line_polyline(
    decoded: Dict[str, Any],
    *,
    lap_index: int = 0,
    stride: int = 4,
) -> Dict[str, Any]:
    """XZ racing line for one spline-complete candidate lap."""
    segs = decoded.get("lap_segments") or []
    if not segs:
        # full session downsample
        idx = np.arange(0, decoded["count"], stride)
    else:
        lap_index = max(0, min(lap_index, len(segs) - 1))
        seg = segs[lap_index]
        idx = np.arange(seg["i0"], seg["i1"], stride)
    return {
        "lap_index": lap_index if segs else None,
        "points": [
            {
                "x": float(decoded["x"][i]),
                "z": float(decoded["z"][i]),
                "y": float(decoded["y"][i]),
                "spline": float(decoded["spline"][i]),
                "heading_deg": float(decoded["heading_deg"][i]),
                "corner": str(decoded["corner_id"][i]),
            }
            for i in idx
        ],
    }
