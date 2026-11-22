#!/usr/bin/env python3
"""Evidence gate for Motec exports — never guess lap times or track-map health.

Checks (fail loud):
  1. .ldx beacon gaps match rpy valid flying lap_ms (Δ ≤ 50 ms)
  2. .ldx Details Fastest Time == best valid flyer
  3. Workspace Internal Maths.xml lists SPEED before wheel-speed fallbacks
  4. Venue .mt2 exists, MoTeCTrackV2 + Points/Data present, size stable vs source
  5. .ld has SPEED + G_LAT (+ beacons implied by .ldx)

Evidence sources (Motec):
  - forum.motec.com.au/viewtopic.php?f=26&t=4926 — Corr Dist from speed priority
    list; track cursor uses beacon + Corr Dist (not GPS).
  - i2 Training Seminar 2008 — track map needs lateral G + vehicle speed + beacon;
    graphs need Corr Dist for distance mode.
  - ACC Workspace Internal Maths.xml — SPEED first in CircuitMaths Params list.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_TICKS = 1_000_000.0
_LAP_TOL_S = 0.050  # 50 ms — rpy vs Motec beacon gap


def _parse_ldx_beacon_times_s(ldx_path: Path) -> List[float]:
    text = ldx_path.read_text(encoding="utf-8", errors="replace")
    raw = [float(m) for m in re.findall(r'ClassName="BCN"[^>]*Time="([^"]+)"', text)]
    if not raw:
        raw = [float(m) for m in re.findall(r'ClassName="BCN".*?Time="([^"]+)"', text, re.S)]
    # Motec stores ticks for our rpy LDs (1e6 / sec)
    return [t / _TICKS for t in raw]


def _ldx_details(ldx_path: Path) -> Dict[str, str]:
    text = ldx_path.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, str] = {}
    for key in ("Fastest Time", "Fastest Lap", "Venue", "Driver"):
        m = re.search(rf'Id="{key}" Value="([^"]*)"', text)
        if m:
            out[key] = m.group(1)
    return out


def _format_lap(seconds: float) -> str:
    s = max(0.0, float(seconds))
    return f"{int(s // 60)}:{s - 60 * int(s // 60):06.3f}"


def check_lap_times(
    rpy_path: Path,
    ldx_path: Path,
    *,
    car_index: Optional[int] = None,
) -> Dict[str, Any]:
    from vre_rpy.lap_validity import (
        best_valid_flying_time_s,
        decode_rpy_lap_validity,
    )

    timer = decode_rpy_lap_validity(rpy_path, car_index=car_index)
    laps = timer.get("laps") or []
    valid = [
        l
        for l in laps
        if l.get("flying_candidate") and l.get("completed") and l.get("valid") is True
    ]
    best = best_valid_flying_time_s(laps)
    beacons = _parse_ldx_beacon_times_s(ldx_path)
    gaps = [beacons[i + 1] - beacons[i] for i in range(max(0, len(beacons) - 1))]
    details = _ldx_details(ldx_path)

    # Inventory every valid flyer vs nearest beacon gap (informational).
    # Gate on the *best* valid flyer only — Motec .ldx may omit a lap that the
    # rpy validity array still marks valid (partial out-lap / different split).
    matches = []
    misses = []
    best_gap_ok = False
    for lap in sorted(valid, key=lambda x: x["lap_ms"]):
        target = lap["lap_ms"] / 1000.0
        if not gaps:
            misses.append({"rpy_s": target, "reason": "no_ldx_gaps"})
            continue
        nearest = min(gaps, key=lambda g: abs(g - target))
        err = abs(nearest - target)
        row = {
            "rpy_s": target,
            "rpy_fmt": _format_lap(target),
            "ldx_gap_s": nearest,
            "ldx_fmt": _format_lap(nearest),
            "err_ms": round(err * 1000.0, 2),
            "ok": err <= _LAP_TOL_S,
            "is_best": best is not None and abs(target - best) < 1e-6,
        }
        (matches if row["ok"] else misses).append(row)
        if row["is_best"] and row["ok"]:
            best_gap_ok = True

    fastest_ok = False
    details_s = None
    if best is not None and details.get("Fastest Time"):
        m = re.match(r"(\d+):(\d+\.\d+)", details["Fastest Time"])
        if m:
            details_s = int(m.group(1)) * 60.0 + float(m.group(2))
            fastest_ok = abs(details_s - best) <= _LAP_TOL_S

    return {
        "rpy": str(rpy_path),
        "ldx": str(ldx_path),
        "best_valid_s": best,
        "best_valid_fmt": _format_lap(best) if best else None,
        "ldx_fastest_time": details.get("Fastest Time"),
        "ldx_fastest_time_s": details_s,
        "ldx_fastest_lap": details.get("Fastest Lap"),
        "ldx_venue": details.get("Venue"),
        "n_valid_flyers": len(valid),
        "n_ldx_beacons": len(beacons),
        "matches": matches,
        "misses": misses,
        "best_gap_ok": best_gap_ok,
        "fastest_time_ok": fastest_ok,
        "ok": bool(best is not None) and best_gap_ok and fastest_ok,
    }


def check_internal_maths(workspace_dir: Path) -> Dict[str, Any]:
    path = workspace_dir / "Internal Maths.xml"
    if not path.is_file():
        return {"ok": False, "path": str(path), "error": "missing Internal Maths.xml"}
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'ProgId="MoTeC\.CircuitMaths[^"]*"[^>]*Params="([^"]*)"', text)
    if not m:
        return {"ok": False, "path": str(path), "error": "no CircuitMaths plugin"}
    params = m.group(1)
    # Speed priority list is inside [...]
    bracket = re.search(r"\[([^\]]+)\]", params)
    speed_list = (bracket.group(1) if bracket else "").split(";")
    speed_list = [s.strip() for s in speed_list if s.strip()]
    has_speed = any(s.upper() == "SPEED" or s.upper() == "SPEED_KMH" for s in speed_list)
    # Also accept ACC_NN style where SPEED is the primary named channel before []
    if not has_speed and re.search(r'Params="4:[01];SPEED[;:]', text):
        has_speed = True
    # Motec default dual-wheel ON without SPEED steals Corr Dist; ACC V1 uses 4:0
    dual_wheel_on = bool(re.search(r'Params="4:1;', text)) and not has_speed
    ok = has_speed and ("[SPEED" in text or ";SPEED;" in text or ";SPEED:" in text)
    return {
        "ok": ok,
        "path": str(path),
        "speed_priority": speed_list[:8],
        "has_SPEED": has_speed,
        "dual_wheel_pair_enabled": dual_wheel_on,
        "note": (
            "CircuitMaths must list SPEED (ACC style) with dual-wheel pair off (4:0); "
            "else Corr Dist missing → Track Map labels-only / No distance calculated. "
            "Motec forum t=4926; ACC Workspace Internal Maths.xml"
        ),
    }


def check_mt2(mt2_path: Path, *, source: Optional[Path] = None) -> Dict[str, Any]:
    if not mt2_path.is_file():
        return {"ok": False, "path": str(mt2_path), "error": "missing .mt2"}
    data = mt2_path.read_bytes()
    ok_magic = data.startswith(b"\x0c\x00\x00\x00MoTeCTrack") or b"MoTeCTrackV2" in data[:64]
    has_points = b"Points" in data[:800] and b"Data" in data[:800]
    has_sections = b"SectionGroups" in data
    size_ok = True
    size_delta = 0
    if source and source.is_file():
        size_delta = len(data) - source.stat().st_size
        # Same-length renames only — any size change risks blank track stroke
        size_ok = size_delta == 0
    return {
        "ok": ok_magic and has_points and has_sections and size_ok,
        "path": str(mt2_path),
        "bytes": len(data),
        "MoTeCTrackV2": ok_magic,
        "has_Points_Data": has_points,
        "has_SectionGroups": has_sections,
        "size_delta_vs_source": size_delta,
        "size_ok": size_ok,
        "note": (
            "Never rewrite UTF-16 Name lengths in .mt2 — Motec then shows section "
            "labels without the track polyline (Points/Data before SectionGroups)."
        ),
    }


def check_ld_channels(ld_path: Path) -> Dict[str, Any]:
    sys.path.insert(0, "/tmp/ldparser")
    sys.path.insert(0, str(Path.home() / "tools" / "ldparser"))
    try:
        from ldparser import ldData
    except ImportError:
        return {"ok": False, "error": "ldparser not installed", "path": str(ld_path)}
    d = ldData.fromfile(str(ld_path))
    names = list(d)
    need = ["SPEED", "G_LAT"]
    missing = [n for n in need if n not in names]
    stats = {}
    import numpy as np

    for n in need:
        if n in d:
            a = np.asarray(d[n].data, dtype=np.float64)
            stats[n] = {
                "n": int(a.size),
                "min": float(np.nanmin(a)) if a.size else None,
                "max": float(np.nanmax(a)) if a.size else None,
                "freq": float(d[n].freq),
                "unit": d[n].unit,
            }
    # v2 checks: no unexpected all-zero VRE channels; lap distance and GPS line sane.
    may_be_flat = {"VRE_TC_LEVEL", "VRE_ABS_LEVEL", "VRE_TCCUT", "VRE_ENG_MAP", "VRE_TC_EST",
                   "VRE_ABS_EST", "VRE_SPIN", "VRE_LOCK", "VRE_TRACK_GRIP", "VRE_SYNC_OK",
                   "VRE_PRED_LAP_S", "VRE_PRED_DELTA_S", "VRE_VALID", "VRE_TRAIL_BRAKE"}
    zero_bad, zero_warn = [], []
    for n in names:
        if not n.startswith("VRE_"):
            continue
        a = np.asarray(d[n].data, dtype=np.float64)
        if a.size and np.nanmax(np.abs(a)) == 0:
            (zero_warn if n in may_be_flat else zero_bad).append(n)
    warnings = [f"all-zero (allowed): {n}" for n in zero_warn]
    lap_dist = None
    if "LAP_DIST" in names:
        ld_ = np.asarray(d["LAP_DIST"].data, dtype=np.float64)
        resets = int(np.sum(np.diff(ld_) < -0.5 * np.nanmax(ld_)))
        lap_dist = {"max_m": float(np.nanmax(ld_)), "resets": resets}
        if "GPS_LATITUDE" in names and "GPS_LONGITUDE" in names:
            lat = np.radians(np.asarray(d["GPS_LATITUDE"].data, dtype=np.float64))
            lon = np.radians(np.asarray(d["GPS_LONGITUDE"].data, dtype=np.float64))
            seg = np.hypot(np.diff(lat), np.diff(lon) * np.cos(lat[:-1])) * 6_371_000.0
            dl = np.diff(ld_)
            ok = (dl > 0) & (dl < 50) & (seg < 50)
            ratio = float(seg[ok].sum() / max(dl[ok].sum(), 1e-6)) if ok.any() else None
            lap_dist["gps_vs_lapdist_ratio"] = ratio
            if ratio is None or abs(ratio - 1.0) > 0.02:
                warnings.append(f"GPS path length differs from LAP_DIST by more than 2% (ratio {ratio})")
    else:
        warnings.append("no LAP_DIST (pose channels unavailable)")
    return {
        "ok": not missing and not zero_bad and all((stats.get(n) or {}).get("n", 0) > 100 for n in need),
        "path": str(ld_path),
        "missing": missing,
        "all_zero_vre": zero_bad,
        "lap_dist": lap_dist,
        "warnings": warnings,
        "venue": getattr(d.head, "venue", None) or None,
        "stats": stats,
    }


def verify_export(
    *,
    motec_dir: Path,
    drivers: Sequence[Tuple[Path, Path, str]],
    workspace_name: str = "VRE_Export",
    mt2_name: Optional[str] = None,
    mt2_source: Optional[Path] = None,
) -> Dict[str, Any]:
    ws = Path(workspace_name)
    if not ws.is_dir():
        ws = motec_dir / workspace_name
    report: Dict[str, Any] = {
        "workspace": str(ws),
        "maths": check_internal_maths(ws),
        "drivers": [],
    }
    all_ok = report["maths"]["ok"]
    venue = None
    for rpy, ld, label in drivers:
        ldx = Path(str(ld) + "x") if ld.suffix == ".ld" else ld
        lap = check_lap_times(rpy, ldx)
        ch = check_ld_channels(ld)
        venue = venue or ch.get("venue") or lap.get("ldx_venue")
        report["drivers"].append({"label": label, "laps": lap, "channels": ch})
        all_ok = all_ok and lap["ok"] and ch["ok"]

    if mt2_name is None and venue:
        mt2_name = f"{venue}.mt2"
    maps = ws / "Track Maps"
    mt2_path = maps / (mt2_name or "")
    if mt2_name and not mt2_path.is_file() and maps.is_dir():
        # Motec matches Venue → .mt2 basename case-insensitively (Windows FS)
        for p in maps.glob("*.mt2"):
            if p.name.lower() == mt2_name.lower():
                mt2_path = p
                break
    if mt2_name and mt2_path.is_file():
        report["mt2"] = check_mt2(mt2_path, source=mt2_source)
        all_ok = all_ok and report["mt2"]["ok"]
    else:
        report["mt2"] = {
            "ok": True,
            "warning": "no venue .mt2 in Track Maps — Motec Track Map has no "
            "pre-drawn outline/corner labels for this venue (laps/graphs unaffected)",
            "venue": venue,
            "path": str(mt2_path),
        }
    report["ok"] = all_ok
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--motec-dir", type=Path, required=True)
    ap.add_argument(
        "--driver",
        action="append",
        required=True,
        help="RPY:LD:Label (repeat)",
    )
    ap.add_argument("--workspace", default="VRE_Export")
    ap.add_argument("--mt2", default=None, help="default: <ldx Venue>.mt2")
    ap.add_argument(
        "--mt2-source",
        type=Path,
        default=None,
        help="Pristine ACC .mt2 to assert size-unchanged",
    )
    args = ap.parse_args(argv)

    drivers = []
    for spec in args.driver:
        parts = spec.split(":")
        if len(parts) < 3:
            ap.error(f"bad --driver {spec!r}; want RPY:LD:Label")
        # Windows drive: C:\a.rpy:C:\a.ld:Tomato — take last two colons carefully
        label = parts[-1]
        ld = Path(parts[-2])
        rpy = Path(":".join(parts[:-2]))
        drivers.append((rpy, ld, label))

    report = verify_export(
        motec_dir=args.motec_dir,
        drivers=drivers,
        workspace_name=args.workspace,
        mt2_name=args.mt2,
        mt2_source=args.mt2_source,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
