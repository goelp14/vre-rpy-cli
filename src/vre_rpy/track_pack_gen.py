"""Generate a track pack (corners, directions, terrain) from ACC's fastlane.ai — any track.

Corners are runs of significant curvature along the AI line (merged into complexes), their direction
comes from the curvature sign, speed class from the tightest radius. Terrain features (crests, dips)
come from the vertical curvature of the line's elevation. Curated names (``track_pack/<track>_acc.json``)
are attached by apex position; otherwise corners are named T1..Tn. Optional per-track notes
(``track_pack/notes/<track>.json``) only add labels and cues — they never change detection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

PACK_DIR = Path(__file__).resolve().parent / "track_pack"
GEN_DIR = PACK_DIR / "generated"
NOTES_DIR = PACK_DIR / "notes"
STEP_M = 2.0
SCHEMA = "vre.track_pack.generated.v1"

# Detection parameters (track-agnostic). A corner is a run where the AI line turns faster than
# 1/R_DETECT for long enough to change heading by MIN_TURN_DEG.
R_DETECT_M = 450.0
MIN_TURN_DEG = 12.0
MERGE_GAP_M = 30.0
COMPLEX_GAP_M = 80.0
VERT_CURV_THR = 0.0012  # 1/m: ~0.1 g of load change at 100 km/h


def _resample_closed(xyz: np.ndarray, step: float):
    seg = np.linalg.norm(np.diff(np.vstack([xyz, xyz[:1]]), axis=0), axis=1)
    cum = np.r_[0.0, np.cumsum(seg)]
    L = float(cum[-1])
    s = np.arange(0.0, L, step)
    closed = np.vstack([xyz, xyz[:1]])
    out = np.c_[[np.interp(s, cum, closed[:, k]) for k in range(3)]].T
    return s, out, L


def _smooth_circ(a: np.ndarray, win_pts: int) -> np.ndarray:
    if win_pts <= 1:
        return a
    k = np.ones(win_pts) / win_pts
    pad = win_pts
    ext = np.r_[a[-pad:], a, a[:pad]]
    return np.convolve(ext, k, mode="same")[pad:-pad]


def _runs(mask: np.ndarray) -> List[List[int]]:
    """Contiguous True runs on a circular array as [start, end] (end exclusive, may exceed n)."""
    n = len(mask)
    if mask.all():
        return [[0, n]]
    shift = int(np.argmin(mask))  # start scanning at a False sample
    m = np.roll(mask, -shift)
    edges = np.diff(np.r_[0, m.astype(int), 0])
    st, en = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
    return [[int(a + shift), int(b + shift)] for a, b in zip(st, en)]


def generate(geo: Dict[str, Any], *, track_id: str) -> Dict[str, Any]:
    xyz = np.asarray(geo["centerline_xyz"], dtype=np.float64)
    s, p, L = _resample_closed(xyz, STEP_M)
    n = len(s)
    xz = p[:, [0, 2]]
    d = np.gradient(np.vstack([xz[-1:], xz, xz[:1]]), axis=0)[1:-1]
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
    d2 = np.roll(d, -1, axis=0)
    cross = d[:, 0] * d2[:, 1] - d[:, 1] * d2[:, 0]   # > 0: right turn in (x, z)
    dot = np.clip((d * d2).sum(1), -1, 1)
    kappa = np.arctan2(cross, dot) / STEP_M            # signed curvature, 1/m
    k_s = _smooth_circ(kappa, int(30 / STEP_M))
    runs = _runs(np.abs(k_s) > 1.0 / R_DETECT_M)
    # merge same-direction runs separated by short straights
    merged: List[List[int]] = []
    for a, b in runs:
        sign = np.sign(np.sum(k_s[np.arange(a, b) % n]))
        if merged and (a - merged[-1][1]) * STEP_M < MERGE_GAP_M and merged[-1][2] == sign:
            merged[-1][1] = b
        else:
            merged.append([a, b, sign])
    corners = []
    for a, b, sign in merged:
        idx = np.arange(a, b) % n
        turn = float(np.degrees(np.abs(np.sum(kappa[idx])) * STEP_M))
        if turn < MIN_TURN_DEG:
            continue
        apex = idx[int(np.argmax(np.abs(k_s[idx])))]
        r_min = 1.0 / max(float(np.max(np.abs(k_s[idx]))), 1e-6)
        corners.append({
            "direction": "right" if sign > 0 else "left",
            "spline_start": round(float(s[a % n] / L), 4),
            "spline_end": round(float(s[(b - 1) % n] / L), 4),
            "apex_spline": round(float(s[apex] / L), 4),
            "start_m": round(float(s[a % n]), 1),
            "end_m": round(float(s[(b - 1) % n]), 1),
            "apex_m": round(float(s[apex]), 1),
            "turn_deg": round(turn, 1),
            "min_radius_m": round(r_min, 1),
            "speed_class": "slow" if r_min < 60 else ("medium" if r_min < 160 else "fast"),
        })
    corners.sort(key=lambda c: c["apex_m"])
    # complexes: consecutive corners closer than COMPLEX_GAP_M
    cx = 0
    for i, c in enumerate(corners):
        if i and (c["start_m"] - corners[i - 1]["end_m"]) > COMPLEX_GAP_M:
            cx += 1
        c["complex"] = cx
        c["name"] = f"T{i + 1}"
        c["display_name"] = f"T{i + 1}"

    y = _smooth_circ(p[:, 1], int(20 / STEP_M))
    grade = np.gradient(np.r_[y[-1], y, y[0]])[1:-1] / STEP_M
    kv = _smooth_circ(np.gradient(np.r_[grade[-1], grade, grade[0]])[1:-1] / STEP_M, int(20 / STEP_M))
    terrain = []
    for kind, mask in (("crest", kv < -VERT_CURV_THR), ("dip", kv > VERT_CURV_THR)):
        for a, b in _runs(mask):
            if (b - a) * STEP_M < 10:
                continue
            idx = np.arange(a, b) % n
            k = idx[int(np.argmax(np.abs(kv[idx])))]
            terrain.append({
                "kind": kind,
                "start_m": round(float(s[a % n]), 1),
                "end_m": round(float(s[(b - 1) % n]), 1),
                "peak_m": round(float(s[k]), 1),
                "peak_spline": round(float(s[k] / L), 4),
                "vert_curv_per_m": round(float(kv[k]), 5),
                # load change in g at 100 km/h; scales with speed squared
                "dg_at_100kmh": round(float((100 / 3.6) ** 2 * kv[k] / 9.81), 3),
                "grade_pct": round(float(100 * grade[k]), 2),
            })
    terrain.sort(key=lambda t: t["peak_m"])
    return {
        "schema": SCHEMA,
        "track_id": track_id,
        "length_m": round(L, 1),
        "elevation_range_m": round(float(y.max() - y.min()), 1),
        "corners": corners,
        "terrain": terrain,
        "params": {"r_detect_m": R_DETECT_M, "min_turn_deg": MIN_TURN_DEG, "merge_gap_m": MERGE_GAP_M,
                   "complex_gap_m": COMPLEX_GAP_M, "vert_curv_thr_per_m": VERT_CURV_THR, "step_m": STEP_M},
        "source": "fastlane.ai (AI line); corners from curvature, terrain from elevation",
    }


def attach_curated_names(pack: Dict[str, Any], curated: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Name generated corners from a curated config (by apex inside the curated range / nearest)."""
    if not curated:
        return pack
    cur = curated.get("corners") or []
    mism = []
    for c in pack["corners"]:
        a = c["apex_spline"]
        # curated windows are hand-drawn and often misplaced: accept only a nearby curated apex
        def _apex(k):
            return k.get("apex_spline", (k.get("spline_start", 0) + k.get("spline_end", 0)) / 2)

        near = min(cur, key=lambda k: abs(_apex(k) - a), default=None)
        tol = 100.0 / max(pack["length_m"], 1.0)
        hit = [near] if near is not None and abs(_apex(near) - a) <= tol else []
        if hit:
            k = hit[0]
            c["display_name"] = k.get("display_name") or k.get("name") or c["display_name"]
            c["curated_name"] = k.get("name")
            c["name_source"] = "curated (approximate position)"
            if k.get("direction") and k["direction"] != c["direction"]:
                mism.append({"corner": c["display_name"], "curated": k["direction"], "generated": c["direction"]})
    pack["curated_direction_mismatches"] = mism
    if curated.get("sectors"):
        pack["sectors"] = curated["sectors"]
    pack["display_name"] = curated.get("display_name")
    return pack


def attach_notes(pack: Dict[str, Any], notes: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Add researched labels/cues. Never changes positions, directions or thresholds."""
    if not notes:
        return pack
    for n in notes.get("corners") or []:
        lo, hi = n["apex_m_range"]
        for c in pack["corners"]:
            if lo <= c["apex_m"] <= hi:
                c["display_name"] = n["name"]
                c["name_source"] = "notes"
                c["notes"] = {k: v for k, v in n.items() if k not in ("name", "apex_m_range")}
    pack["notes"] = {k: v for k, v in notes.items() if k != "corners"}
    pack["notes_applied"] = True
    return pack


def load_track_pack(track: str) -> Optional[Dict[str, Any]]:
    """Generated pack (+ curated names, + notes) for a track folder name, or None."""
    key = track.lower().removesuffix("_acc")
    p = GEN_DIR / f"{key}.json"
    if not p.is_file():
        return None
    pack = json.loads(p.read_text(encoding="utf-8"))
    nf = NOTES_DIR / f"{key}.json"
    if nf.is_file():
        pack = attach_notes(pack, json.loads(nf.read_text(encoding="utf-8")))
    return pack


def build_pack(track: str, cache_dir=None) -> Dict[str, Any]:
    from .acc_fastlane import parse_acc_fastlane, resolve_fastlane

    geo = parse_acc_fastlane(resolve_fastlane(track, cache_dir=cache_dir))
    pack = generate(geo, track_id=track)
    cur = PACK_DIR / f"{track}_acc.json"
    curated = json.loads(cur.read_text(encoding="utf-8")) if cur.is_file() else None
    return attach_curated_names(pack, curated)
