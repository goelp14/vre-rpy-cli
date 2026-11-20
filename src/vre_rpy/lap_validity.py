"""Per-lap validity from the .rpy lap-timer snapshot array.

Layout (24-byte records; same array ``parse_rpy.extract_lap_times_ms`` uses):

    +0   u32  current-lap clock, ms (resets at S/F)
    +4   u32  reference/predicted lap ms
    +8   u8   valid flag: 1 at lap start, 0 after first track-limit cut until S/F
    +12  u32  per-car/session constant
    +16  f64  session time, seconds (monotonic)

Empirical across Spa fixtures: flag drops at known track-limit hotspots;
clean flying laps stay 1 for the full lap. Motec has no validity channel —
treat as ``file_go_empirical``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

RPY_VALIDITY_GO = True
RPY_VALIDITY_STATUS = "file_go_empirical"
RPY_VALIDITY_EVIDENCE = (
    "arr24[+8] u8: 1 at lap start, 0 after first cut; flips repeat at Spa "
    "track-limit hotspots across 3 fixtures; Erik official 2:15.425 lap all-1"
)

_NO_REF = 0x7FFFFFFF
_RESET_DROP_MS = 1000
_FLYING_MIN_MS = 60_000
_FLYING_MAX_MS = 600_000


def find_lap_timer_array(ex, car_index: int = 0):
    """Return the stride-24 lap-timer ArrayBlob (largest) for a car, or None."""
    if not ex.car_records or car_index >= len(ex.car_records):
        return None
    snaps = ex.car_records[car_index].snapshots
    if not snaps:
        return None
    cands = [a for a in snaps[0].arrays if a.stride == 24 and a.count >= 100]
    return max(cands, key=lambda a: a.count) if cands else None


def _columns(data: bytes) -> Dict[str, np.ndarray]:
    n = len(data) // 24
    raw = np.frombuffer(data[: n * 24], dtype=np.uint8).reshape(n, 24)
    u32 = np.frombuffer(data[: n * 24], dtype="<u4").reshape(n, 6)
    time_s = np.frombuffer(np.ascontiguousarray(raw[:, 16:24]).tobytes(), dtype="<f8")
    return {
        "lap_ms": u32[:, 0].astype(np.int64),
        "ref_ms": u32[:, 1].astype(np.int64),
        "valid": raw[:, 8].copy(),
        "time_s": time_s,
    }


def decode_lap_validity(data: bytes) -> List[Dict[str, Any]]:
    """Split the lap-timer array into laps and report per-lap validity.

    The completed-lap marker arrives a few samples *after* S/F (``lap_ms`` large
    while neighbours are ~0, ``ref_ms == 0``, own valid byte is 0 — uninformative).
    Markers are stripped before the run split and only used to flag ``completed``.
    """
    c = _columns(data)
    t, ref, flag, ts = c["lap_ms"], c["ref_ms"], c["valid"], c["time_s"]
    n = len(t)
    if n == 0:
        return []

    # Lap-complete marker: big lap clock with ref_ms == 0, sandwiched between
    # next-lap samples whose clock is back near 0.
    marker = (t > _FLYING_MIN_MS) & (ref == 0)
    if n > 2:
        near0_prev = np.r_[False, t[:-1] < 5000]
        near0_next = np.r_[t[1:] < 5000, False]
        marker &= near0_prev & near0_next
    marker_idx = np.nonzero(marker)[0]

    keep = np.nonzero(~marker)[0]
    runs: List[np.ndarray] = []
    start = 0
    for k in range(1, len(keep)):
        if t[keep[k]] + _RESET_DROP_MS < t[keep[k - 1]]:
            runs.append(keep[start:k])
            start = k
    if len(keep):
        runs.append(keep[start:])

    laps: List[Dict[str, Any]] = []
    for lap_index, run in enumerate(runs):
        end_rec = int(run[-1])
        end_ms = int(t[end_rec])
        # first marker after the run's last in-lap sample
        after = marker_idx[marker_idx > end_rec]
        marker_ms: Optional[int] = None
        if len(after) and (after[0] - end_rec) <= 16:
            marker_ms = int(t[after[0]])
        completed = marker_ms is not None
        lap_ms = end_ms  # last in-lap clock sample (Motec-locked, Δ≤7 ms)
        f = flag[run]
        bad = np.nonzero(f == 0)[0]
        invalid_at = int(t[run[bad[0]]]) if len(bad) else None
        invalid_time_s = float(ts[run[bad[0]]]) if len(bad) else None
        flying = completed and _FLYING_MIN_MS <= lap_ms <= _FLYING_MAX_MS
        laps.append(
            {
                "lap_index": lap_index,
                "rec_start": int(run[0]),
                "rec_end": end_rec,
                "n_records": int(len(run)),
                "lap_ms": int(lap_ms),
                "marker_ms": marker_ms,
                "completed": bool(completed),
                "flying_candidate": bool(flying),
                "valid": bool(len(bad) == 0),
                "invalid_at_lap_ms": invalid_at,
                "invalid_at_session_s": invalid_time_s,
                "session_start_s": float(ts[run[0]]),
                "session_end_s": float(ts[end_rec]),
            }
        )
    return laps


def decode_rpy_lap_validity(path: str | Path, car_index: int = 0) -> Dict[str, Any]:
    from .processor import extract_rpy

    ex = extract_rpy(path)
    arr = find_lap_timer_array(ex, car_index)
    if arr is None:
        return {
            "RPY_VALIDITY_GO": False,
            "laps": [],
            "note": "no stride-24 lap-timer array",
        }
    laps = decode_lap_validity(arr.data)
    return {
        "RPY_VALIDITY_GO": RPY_VALIDITY_GO,
        "status": RPY_VALIDITY_STATUS,
        "evidence": RPY_VALIDITY_EVIDENCE,
        "source": f"snapshot arr_24 @file {arr.offset} +8 u8",
        "laps": laps,
    }


def _lap_role(duration_s: float, *, completed: bool, flying: bool, is_last: bool) -> str:
    if flying and completed:
        return "flying"
    if duration_s >= 300.0:
        return "garage_or_pit"
    if is_last and not completed:
        return "in_lap"
    if not completed and duration_s < 90.0:
        return "out_or_partial"
    if completed:
        return "completed"
    return "incomplete"


def build_labeled_laps(
    timer_laps: Sequence[Dict[str, Any]],
    beacon_times_s: Sequence[float],
    *,
    venue: str = "ACC",
    preferred_fastest_s: Optional[float] = None,
    pose_segments: Optional[Sequence[Dict[str, Any]]] = None,
    motec_t0: float = 0.0,
) -> Dict[str, Any]:
    """Motec-aligned lap labels for AI packs (same beacons as .ldx).

    Motec Lap N is the gap between consecutive BCN markers. Timer rows from
    ``decode_lap_validity`` supply validity / lap_ms; pose segments attach when
    durations match. ``beacon_times_s`` must be Motec-relative (session - t0).
    """
    beacons = [float(x) for x in beacon_times_s]
    t0 = float(motec_t0)
    gaps = [
        beacons[i + 1] - beacons[i] for i in range(max(0, len(beacons) - 1))
    ]
    # Match timer laps to Motec gaps by (session_end - t0) ≈ beacon end
    timer_by_end: List[Dict[str, Any]] = [dict(l) for l in timer_laps]
    labeled: List[Dict[str, Any]] = []
    for i, dur in enumerate(gaps):
        start_s = beacons[i]
        end_s = beacons[i + 1]
        best: Optional[Dict[str, Any]] = None
        best_err = 1e9
        for lap in timer_by_end:
            err = abs(float(lap["session_end_s"]) - t0 - end_s)
            if err < best_err and err < 1.5:
                best_err = err
                best = lap
        completed = bool(best.get("completed")) if best else False
        flying = bool(best.get("flying_candidate")) if best else (
            60.0 <= dur <= 600.0 and dur >= 120.0
        )
        lap_ms = int(best["lap_ms"]) if best else int(round(dur * 1000.0))
        valid = best.get("valid") if best else None
        role = _lap_role(
            dur,
            completed=completed,
            flying=bool(flying and completed),
            is_last=(i == len(gaps) - 1),
        )
        pose_match = None
        if pose_segments and best and best.get("lap_ms"):
            for s in pose_segments:
                if s.get("lap_ms") == best.get("lap_ms"):
                    pose_match = {
                        "i0": s.get("i0"),
                        "i1": s.get("i1"),
                        "n_samples": s.get("n_samples"),
                        "valid_flag": s.get("valid_flag"),
                    }
                    break
        labeled.append(
            {
                "motec_lap": i + 1,
                "session_start_s": start_s,
                "session_end_s": end_s,
                "duration_s": dur,
                "lap_ms": lap_ms,
                "completed": completed,
                "flying_candidate": bool(flying and completed)
                if best
                else bool(120.0 <= dur <= 180.0),
                "valid": valid,
                "invalid_at_lap_ms": best.get("invalid_at_lap_ms") if best else None,
                "role": role,
                "timer_match_err_s": None if best is None else best_err,
                "pose": pose_match,
                "label": f"Lap {i + 1}",
            }
        )

    # Fastest Motec lap must be a VALID flying lap when validity is known.
    valid_flyers = [
        L for L in labeled if L.get("flying_candidate") and L.get("valid") is True
    ]
    flyers = valid_flyers or [L for L in labeled if L.get("flying_candidate")]
    fastest_valid_only = bool(valid_flyers)
    if preferred_fastest_s is not None and flyers:
        fastest_s = float(preferred_fastest_s)
        fastest_idx = min(
            flyers, key=lambda L: abs(float(L["duration_s"]) - fastest_s)
        )["motec_lap"]
    elif flyers:
        fastest_s = min(float(L["duration_s"]) for L in flyers)
        fastest_idx = next(
            L["motec_lap"]
            for L in flyers
            if abs(float(L["duration_s"]) - fastest_s) < 1e-6
        )
    elif labeled:
        fastest_s = min(float(L["duration_s"]) for L in labeled)
        fastest_idx = labeled[0]["motec_lap"]
    else:
        fastest_s = 0.0
        fastest_idx = 1

    return {
        "id": "laps",
        "status": "file_go",
        "venue": venue,
        "motec_beacons_s": beacons,
        "total_laps": len(labeled),
        "fastest_time_s": fastest_s,
        "fastest_motec_lap": int(fastest_idx),
        "fastest_label": f"Lap {int(fastest_idx)}",
        "fastest_valid_only": fastest_valid_only,
        "laps": labeled,
        "note": (
            "Motec Lap N = gap between consecutive .ldx BCN markers; same times "
            "as write_ldx_beacons / Motec Laps pane. Fastest prefers valid flying "
            "laps (arr24[+8]) when present."
        ),
    }


def best_valid_flying_time_s(
    timer_or_labeled_laps: Sequence[Dict[str, Any]] | Dict[str, Any],
) -> Optional[float]:
    """Fastest completed flying lap time (s) among laps with ``valid is True``.

    Accepts either a timer-lap list (``lap_ms``) or a ``build_labeled_laps``
    dict / its ``laps`` list. Prefers ``lap_ms`` over Motec ``duration_s`` so a
    short mis-aligned beacon gap cannot beat a real flying lap. Returns None if
    no valid flyer in ``[_FLYING_MIN_MS, _FLYING_MAX_MS]``.
    """
    if isinstance(timer_or_labeled_laps, dict):
        rows = list(timer_or_labeled_laps.get("laps") or [])
    else:
        rows = list(timer_or_labeled_laps)
    times: List[float] = []
    for lap in rows:
        if not lap.get("flying_candidate"):
            continue
        if lap.get("valid") is not True:
            continue
        if "completed" in lap and not lap.get("completed"):
            continue
        if lap.get("lap_ms"):
            t = float(lap["lap_ms"]) / 1000.0
        elif "duration_s" in lap:
            t = float(lap["duration_s"])
        else:
            continue
        if not (_FLYING_MIN_MS / 1000.0 <= t <= _FLYING_MAX_MS / 1000.0):
            continue
        times.append(t)
    return min(times) if times else None


def order_drivers_main_ref(
    drivers: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pick Motec Main / Ref from 2+ drivers by fastest *valid* flying lap.

    Heuristic (multi-.rpy exports):
      - **Ref** = driver whose best valid flying lap is the overall fastest
      - **Main** = first remaining driver in the original input order

    Single-driver: Main only (no Ref). If no driver has a valid flyer, keep
    input order and mark ``ref_reason``.
    """
    drivers_list = [dict(d) for d in drivers]
    if len(drivers_list) <= 1:
        return {
            "drivers": drivers_list,
            "main_index": 0 if drivers_list else None,
            "ref_index": None,
            "ref_reason": "single_driver",
            "ref_valid_time_s": best_valid_flying_time_s(drivers_list[0].get("laps") or {})
            if drivers_list
            else None,
        }

    scored: List[Tuple[int, float]] = []
    for i, d in enumerate(drivers_list):
        t = best_valid_flying_time_s(d.get("laps") or {})
        if t is not None:
            scored.append((i, t))

    if not scored:
        return {
            "drivers": drivers_list,
            "main_index": 0,
            "ref_index": 1 if len(drivers_list) > 1 else None,
            "ref_reason": "no_valid_flying_laps_keep_input_order",
            "ref_valid_time_s": None,
        }

    ref_i, ref_t = min(scored, key=lambda p: (p[1], p[0]))
    main_i = next((i for i in range(len(drivers_list)) if i != ref_i), None)
    ordered: List[Dict[str, Any]] = []
    if main_i is not None:
        ordered.append(drivers_list[main_i])
    ordered.append(drivers_list[ref_i])
    for i, d in enumerate(drivers_list):
        if i not in (main_i, ref_i):
            ordered.append(d)

    return {
        "drivers": ordered,
        "main_index": 0,
        "ref_index": 1,
        "ref_reason": "fastest_valid_flying_lap",
        "ref_label": drivers_list[ref_i].get("label"),
        "main_label": drivers_list[main_i].get("label") if main_i is not None else None,
        "ref_valid_time_s": ref_t,
        "original_ref_index": ref_i,
        "original_main_index": main_i,
    }


def attach_validity_to_segments(
    segments: Sequence[Dict[str, Any]],
    laps: Sequence[Dict[str, Any]],
    *,
    tol: float = 0.03,
) -> List[Dict[str, Any]]:
    """Fill ``valid_flag`` on pose spline-wrap segments from timer laps.

    Pose and lap-timer clocks are matched in order by lap duration (pose
    samples per ms is constant); unmatched segments keep ``valid_flag=None``.
    """
    flying = [l for l in laps if l.get("flying_candidate")]
    segs = [dict(s) for s in segments]
    if not segs or not flying:
        return segs
    rate = float(np.median([s["n_samples"] for s in segs])) / float(
        np.median([l["lap_ms"] for l in flying])
    )
    ptr = 0
    for s in segs:
        want = s["n_samples"] / rate
        for j in range(ptr, len(flying)):
            if abs(flying[j]["lap_ms"] - want) <= tol * want:
                lap = flying[j]
                s["valid_flag"] = lap["valid"]
                s["lap_ms"] = lap["lap_ms"]
                s["invalid_at_lap_ms"] = lap["invalid_at_lap_ms"]
                s["status"] = "timer_matched"
                ptr = j + 1
                break
    return segs


def main() -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Per-lap validity from .rpy lap-timer array")
    p.add_argument("rpy")
    p.add_argument("--car", type=int, default=0)
    args = p.parse_args()
    json.dump(decode_rpy_lap_validity(args.rpy, args.car), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
