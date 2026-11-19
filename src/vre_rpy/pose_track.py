"""Pose + track-relative channels from .rpy pose samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .processor import ArrayBlob, extract_rpy

_TRACK_PACK = Path(__file__).resolve().parent / "track_pack"


def find_pose(ex, car_index: int = 0) -> Optional[ArrayBlob]:
    if not ex.car_records or car_index >= len(ex.car_records):
        return None
    snaps = ex.car_records[car_index].snapshots
    if not snaps:
        return None
    for a in snaps[0].arrays:
        if a.stride == 0x68 and a.count > 0:
            return a
    return None


def load_track_config(track_id: str) -> Dict[str, Any]:
    key = track_id.strip().lower().replace("-", "_")
    candidates = [
        _TRACK_PACK / f"{key}.json",
        _TRACK_PACK / f"{key}_acc.json",
        _TRACK_PACK / "spa_acc.json" if "spa" in key else None,
        _TRACK_PACK / "monza_acc.json" if "monza" in key else None,
    ]
    for c in candidates:
        if c and c.is_file():
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
        "lap_segments": lap_segments,
        "corner_id": corner_id,
        "layout": {
            "position": "rec_68 f32[0:3] world XYZ (Y up)",
            "spline": "rec_68 f32[23] normalized 0..1 (lap wraps)",
            "heading_deg": "derived atan2(dx,dz) from pose deltas",
            "dist_m": f"spline * track_length_m ({track_length_m})",
            "valid_lap": "file_go_empirical — arr24[+8] u8 via lap_validity (segments matched by lap duration)",
        },
    }


def decode_rpy_pose_track(
    path,
    *,
    track_id: Optional[str] = None,
    car_index: int = 0,
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
            timer_laps = decode_lap_validity(arr24.data)
            decoded["timer_laps"] = timer_laps
            decoded["lap_segments"] = attach_validity_to_segments(
                decoded["lap_segments"], timer_laps
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
        "corner_id": [str(decoded["corner_id"][i]) for i in idx],
        "lap_segments": decoded.get("lap_segments"),
        "layout": decoded.get("layout"),
        "track_config": decoded.get("track_config"),
    }


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
