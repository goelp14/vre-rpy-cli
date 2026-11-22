"""Parse ACC Content/Cache/<track>/fastlane.ai (version 8).

Layout (Spa / Monza / Imola validated):
  [0:8]   i32 version(=8), i32 declared_point_count
  [16:)   centerline: contiguous world-XYZ as 9×f64 records (XYZ + 6 opaque).
          Usable run is ~half of `declared` (early break on non-world XYZ).
  then    side / AI grid section:
          - Monza/Imola: i32 count(=declared) + 16 B pad + count×20×f32
          - Spa: 64 B preamble + n_centerline×20×f32
          Per-point f32[20]: [0]=ideal_speed-ish, [1]=left_width_m, [2]=right_width_m,
          [3:5] often duplicate widths, remainder direction / AI extras.

When count == 2×centerline, subsample even rows to match the centerline.
Official left/right limits = centerline ± normal × (L,R widths).
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# Default Steam install (WSL)
_DEFAULT_ACC_CACHE = Path(
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/"
    "Assetto Corsa Competizione/AC2/Content/Cache"
)

_SIDE_FLOATS = 20
_SYNTHETIC_HALF_W = 7.5


def default_cache_dir() -> Path:
    """ACC ``Content/Cache`` (per-track fastlane.ai). Override with env ``VRE_ACC_CACHE``."""
    import os

    env = os.environ.get("VRE_ACC_CACHE")
    if env:
        return Path(env)
    for cand in (
        _DEFAULT_ACC_CACHE,
        Path("C:/Program Files (x86)/Steam/steamapps/common/Assetto Corsa Competizione/AC2/Content/Cache"),
    ):
        if cand.is_dir():
            return cand
    return _DEFAULT_ACC_CACHE


def resolve_fastlane(
    track_id: str,
    *,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Map filename track tokens → Cache folder names."""
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    key = track_id.strip().lower().replace(" ", "_")
    aliases = {
        "spa": "spa",
        "spa_acc": "spa",
        "spa_francorchamps": "spa",
        "monza": "monza",
        "monza_acc": "monza",
        "imola": "imola",
        "imola_acc": "imola",
    }
    folder = aliases.get(key, key[:-4] if key.endswith("_acc") else key)
    path = cache / folder / "fastlane.ai"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def parse_acc_fastlane(path: Path | str) -> Dict[str, Any]:
    """Return centerline + cumulative distance + official side limits when present."""
    data = Path(path).read_bytes()
    if len(data) < 24:
        raise ValueError("fastlane.ai too small")
    version, declared = struct.unpack_from("<2i", data, 0)
    if version != 8:
        raise ValueError(f"unsupported ACC fastlane version {version} (need 8)")

    pts, rem_off = _parse_centerline(data, declared)
    if len(pts) < 50:
        raise ValueError(f"too few centerline points in {path}: {len(pts)}")

    centerline = [[p[0], p[2]] for p in pts]  # XZ plan (Y = elevation)
    elevation_m = [p[1] for p in pts]
    distance_m: List[float] = [0.0]
    cumulative = 0.0
    for a, b in zip(pts, pts[1:]):
        cumulative += math.dist(a, b)
        distance_m.append(cumulative)

    sides = _parse_side_widths(data[rem_off:], n_centerline=len(pts), declared=declared)
    if sides is not None:
        left_w, right_w, side_meta = sides
        left_limit, right_limit = _limits_from_widths(pts, left_w, right_w)
        limit_note = (
            f"official ACC side widths from fastlane.ai sec2 "
            f"(S={_SIDE_FLOATS} f32; variant={side_meta.get('variant')}, rows={side_meta.get('count')})"
        )
        limits_official = True
        ideal_speed = side_meta.get("ideal_speed")
    else:
        left_limit, right_limit = _synthetic_limits(pts, _SYNTHETIC_HALF_W)
        limit_note = "±7.5m synthetic normals — official side section not decoded"
        limits_official = False
        left_w = right_w = ideal_speed = None

    out: Dict[str, Any] = {
        "version": version,
        "declared_point_count": declared,
        "point_count": len(pts),
        "centerline": centerline,
        "centerline_xyz": [[p[0], p[1], p[2]] for p in pts],
        "left_limit": left_limit,
        "right_limit": right_limit,
        "limit_note": limit_note,
        "cumulative_length_m": round(cumulative, 1),
        "distance_m": distance_m,
        "elevation_m": elevation_m,
        "source": str(path),
        "limits_official": limits_official,
        "side_section_offset": rem_off,
    }
    if left_w is not None and right_w is not None:
        out["left_width_m"] = left_w.tolist()
        out["right_width_m"] = right_w.tolist()
        out["track_width_m_median"] = float(np.median(left_w + right_w))
    if ideal_speed is not None:
        out["ideal_speed"] = ideal_speed.tolist()
    return out


def _parse_centerline(
    data: bytes, declared: int
) -> Tuple[List[Tuple[float, float, float]], int]:
    buf = data[16 : 16 + (len(data) - 16) // 8 * 8]
    doubles = np.frombuffer(buf, dtype="<f8")
    pts: List[Tuple[float, float, float]] = []
    i = 0
    while i + 2 < len(doubles) and len(pts) < max(declared, 1):
        x, y, z = float(doubles[i]), float(doubles[i + 1]), float(doubles[i + 2])
        if not (_looks_world(x, y, z) if pts else _looks_world_loose(x, y, z)):
            if pts:
                break
            i += 1
            continue
        pts.append((x, y, z))
        i += 9
    return pts, 16 + i * 8


def _parse_side_widths(
    rem: bytes, *, n_centerline: int, declared: int
) -> Optional[Tuple[np.ndarray, np.ndarray, Dict[str, Any]]]:
    """Return (left_w, right_w, meta) aligned to centerline, or None."""
    if len(rem) < n_centerline * _SIDE_FLOATS * 4:
        return None

    # Variant A (Monza/Imola): i32 count + 16 B pad + count×20×f32
    if len(rem) >= 24:
        count = struct.unpack_from("<i", rem, 0)[0]
        if 100 < count < 5000 and len(rem) >= 20 + count * _SIDE_FLOATS * 4:
            rows = np.frombuffer(
                rem[20 : 20 + count * _SIDE_FLOATS * 4], dtype="<f4"
            ).reshape(count, _SIDE_FLOATS)
            scored = _score_widths(rows)
            if scored is not None:
                left, right, speed = scored
                left, right, speed = _align_to_centerline(
                    left, right, speed, n_centerline
                )
                if _widths_plausible(left, right):
                    return left, right, {
                        "variant": "count_hdr20",
                        "count": count,
                        "ideal_speed": speed,
                    }

    # Variant B (Spa): 64 B preamble + n_centerline×20×f32
    for hdr in (64, 0, 16, 32, 48, 80):
        need = hdr + n_centerline * _SIDE_FLOATS * 4
        if len(rem) < need:
            continue
        rows = np.frombuffer(rem[hdr:need], dtype="<f4").reshape(
            n_centerline, _SIDE_FLOATS
        )
        scored = _score_widths(rows)
        if scored is None:
            continue
        left, right, speed = scored
        if _widths_plausible(left, right):
            return left, right, {
                "variant": f"preamble_{hdr}",
                "count": n_centerline,
                "ideal_speed": speed,
            }

    # Variant C: declared-length grid with preamble scan
    for hdr in (20, 64, 0):
        need = hdr + declared * _SIDE_FLOATS * 4
        if len(rem) < need:
            continue
        rows = np.frombuffer(rem[hdr:need], dtype="<f4").reshape(
            declared, _SIDE_FLOATS
        )
        scored = _score_widths(rows)
        if scored is None:
            continue
        left, right, speed = scored
        left, right, speed = _align_to_centerline(left, right, speed, n_centerline)
        if _widths_plausible(left, right):
            return left, right, {
                "variant": f"declared_hdr{hdr}",
                "count": declared,
                "ideal_speed": speed,
            }
    return None


def _score_widths(
    rows: np.ndarray,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Pick L/R columns: prefer [1],[2] when ≥90% look like metres."""
    if rows.shape[1] < 3:
        return None
    c1, c2 = rows[:, 1], rows[:, 2]
    if float(((c1 > 0.5) & (c1 < 25)).mean()) >= 0.9 and float(
        ((c2 > 0.5) & (c2 < 25)).mean()
    ) >= 0.9:
        return c1.astype(np.float64), c2.astype(np.float64), rows[:, 0].astype(np.float64)
    return None


def _align_to_centerline(
    left: np.ndarray,
    right: np.ndarray,
    speed: np.ndarray,
    n_centerline: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(left)
    if n == n_centerline:
        return left, right, speed
    if n == 2 * n_centerline:
        return left[0::2], right[0::2], speed[0::2]
    if n > n_centerline:
        return left[:n_centerline], right[:n_centerline], speed[:n_centerline]
    # Too short — pad with median (should not happen on stock Cache files)
    pad_l = float(np.median(left)) if len(left) else _SYNTHETIC_HALF_W
    pad_r = float(np.median(right)) if len(right) else _SYNTHETIC_HALF_W
    pad_s = float(np.median(speed)) if len(speed) else 0.0
    out_l = np.full(n_centerline, pad_l, dtype=np.float64)
    out_r = np.full(n_centerline, pad_r, dtype=np.float64)
    out_s = np.full(n_centerline, pad_s, dtype=np.float64)
    out_l[:n] = left
    out_r[:n] = right
    out_s[:n] = speed
    return out_l, out_r, out_s


def _widths_plausible(left: np.ndarray, right: np.ndarray) -> bool:
    width = left + right
    med = float(np.median(width))
    return 6.0 <= med <= 20.0 and float(((left > 0.5) & (left < 25)).mean()) >= 0.85


def _limits_from_widths(
    pts: Sequence[Tuple[float, float, float]],
    left_w: np.ndarray,
    right_w: np.ndarray,
) -> Tuple[List[List[float]], List[List[float]]]:
    left_limit: List[List[float]] = []
    right_limit: List[List[float]] = []
    for i, (x, _y, z) in enumerate(pts):
        if i + 1 < len(pts):
            fx, fz = pts[i + 1][0] - x, pts[i + 1][2] - z
        else:
            fx, fz = x - pts[i - 1][0], z - pts[i - 1][2]
        flen = math.hypot(fx, fz) or 1.0
        nx, nz = -fz / flen, fx / flen
        lw = float(left_w[i])
        rw = float(right_w[i])
        left_limit.append([x + nx * lw, z + nz * lw])
        right_limit.append([x - nx * rw, z - nz * rw])
    return left_limit, right_limit


def _synthetic_limits(
    pts: Sequence[Tuple[float, float, float]], half_w: float
) -> Tuple[List[List[float]], List[List[float]]]:
    w = np.full(len(pts), half_w, dtype=np.float64)
    return _limits_from_widths(pts, w, w)


def _looks_world_loose(x: float, y: float, z: float) -> bool:
    return (
        math.isfinite(x)
        and math.isfinite(y)
        and math.isfinite(z)
        and abs(x) < 20000
        and abs(z) < 20000
        and -500 < y < 3000
    )


def _looks_world(x: float, y: float, z: float) -> bool:
    # World bounds for any ACC track: km-scale maps, elevation up to mountain circuits
    # (Red Bull Ring's AI line sits at ~665 m; a 500 m cap misaligned the whole parse).
    return (
        math.isfinite(x)
        and math.isfinite(y)
        and math.isfinite(z)
        and abs(x) < 20000
        and abs(z) < 20000
        and -500 < y < 3000
        and not (abs(x) < 1 and abs(z) < 1)
    )


def to_track_geometry(
    fastlane: Dict[str, Any],
    track_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Shape expected by replay-viewer / ac_app track_geometry helpers."""
    length = float(track_config.get("length_m") or fastlane["cumulative_length_m"])
    return {
        "track_id": track_config.get("track_id"),
        "display_name": track_config.get("display_name"),
        "length_m": length,
        "source": fastlane.get("source"),
        "limits_official": fastlane.get("limits_official", False),
        "limit_note": fastlane.get("limit_note"),
        "centerline": fastlane["centerline"],
        "left_limit": fastlane["left_limit"],
        "right_limit": fastlane["right_limit"],
        "distance_m": fastlane["distance_m"],
        "elevation_m": fastlane["elevation_m"],
        "corners": track_config.get("corners", []),
        "sectors": track_config.get("sectors", []),
        "direction": track_config.get("direction"),
        "track_width_m_median": fastlane.get("track_width_m_median"),
    }
