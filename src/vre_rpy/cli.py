#!/usr/bin/env python3
"""vre-rpy: ACC .rpy → AI-labeled laps + MoTeC i2 workspace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_rpy_arg(spec: str) -> Tuple[Path, str]:
    if ":" in spec:
        path_part, _, label = spec.rpartition(":")
        p = Path(path_part)
        if p.exists() or path_part.endswith(".rpy"):
            return p, (label.strip() or p.stem)
    p = Path(spec)
    return p, p.stem


def _parse_driver_spec(spec: str) -> Tuple[Path, Path, str]:
    """RPY:LD:Label — last two colon fields are LD and Label."""
    parts = spec.split(":")
    if len(parts) < 3:
        raise SystemExit(f"bad --driver {spec!r}; want RPY:LD:Label")
    label = parts[-1]
    ld = Path(parts[-2])
    rpy = Path(":".join(parts[:-2]))
    return rpy, ld, label


def cmd_export(args: argparse.Namespace) -> int:
    from vre_rpy.export_rpy_analysis import run

    elec: Optional[Dict[str, Any]] = None
    if args.electronics is not None:
        elec = json.loads(Path(args.electronics).read_text(encoding="utf-8"))
    specs = [_parse_rpy_arg(s) for s in args.rpy]
    summary = run(specs, args.out_dir, car_index=args.car_index, electronics=elec)
    out = {
        "out_dir": str(args.out_dir),
        "drivers": [
            {
                "label": d.get("label"),
                "motec_ld": d.get("motec_ld"),
                "fastest_lap": d.get("motec_fastest_lap"),
                "fastest_time_s": d.get("motec_fastest_time_s"),
            }
            for d in summary.get("drivers") or []
        ],
        "motec_compare_order": summary.get("motec_compare_order"),
        "motec_workspace": summary.get("motec_workspace"),
        "launcher": (summary.get("motec_workspace") or {}).get("launcher_bat"),
        "note": summary.get("note"),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from vre_rpy.verify_motec_export import verify_export

    drivers = [_parse_driver_spec(s) for s in args.driver]
    report = verify_export(
        motec_dir=args.motec_dir,
        drivers=drivers,
        workspace_name=args.workspace,
        mt2_name=args.mt2,
        mt2_source=args.mt2_source,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


def cmd_frames(args: argparse.Namespace) -> int:
    from vre_rpy.replay_frames import write_frames

    specs = [_parse_rpy_arg(s) for s in args.rpy]
    laps: Any = args.laps
    if laps not in ("all", "best"):
        laps = [int(x) for x in str(laps).split(",") if x.strip()]
    index = write_frames(specs, args.out_dir, laps=laps, cache_dir=args.acc_cache)
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir),
                "drivers": [
                    {
                        "label": d["label"],
                        "track": d["identity"].get("track"),
                        "car": d["identity"].get("car"),
                        "sync": d["sync"],
                        "best_lap_index": d["best_lap_index"],
                        "written": len([w for w in d["written"] if "file" in w]),
                    }
                    for d in index["drivers"]
                ],
            },
            indent=2,
            default=float,
        )
    )
    return 0


def cmd_limits(args: argparse.Namespace) -> int:
    from vre_rpy.replay_frames import ReplayFrames
    from vre_rpy.track_limits import analyze

    rfs = []
    for spec in args.rpy:
        path, label = _parse_rpy_arg(spec)
        rf = ReplayFrames(path, car_index=args.car_index, cache_dir=args.acc_cache)
        rf.label = label
        rfs.append(rf)
    tracks = {str(rf.ident.get("track")) for rf in rfs}
    if len(tracks) != 1:
        raise SystemExit(f"all replays must be the same track (got {sorted(tracks)})")
    report = analyze(rfs, zone_model_path=args.zone_model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "track": report["track"], "zones": report["zones"][:12]}, indent=2))
    return 0


def cmd_track_pack(args: argparse.Namespace) -> int:
    from vre_rpy.acc_fastlane import default_cache_dir
    from vre_rpy.track_pack_gen import build_pack

    cache = Path(args.acc_cache) if args.acc_cache else Path(default_cache_dir())
    tracks = args.track or sorted(d.name for d in cache.iterdir() if (d / "fastlane.ai").is_file())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for t in tracks:
        pack = build_pack(t, cache_dir=cache)
        (args.out_dir / f"{t}.json").write_text(json.dumps(pack, indent=1), encoding="utf-8")
        rows.append({"track": t, "length_m": pack["length_m"], "corners": len(pack["corners"]),
                     "terrain_features": len(pack["terrain"])})
    print(json.dumps(rows, indent=1))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from vre_rpy.lap_analysis import run_analysis
    from vre_rpy.replay_frames import ReplayFrames

    p, label = _parse_rpy_arg(args.rpy)
    me = ReplayFrames(p, car_index=args.car_index, cache_dir=args.acc_cache)
    me.label = label
    ref = None
    if args.ref:
        rp, rl = _parse_rpy_arg(args.ref)
        ref = ReplayFrames(rp, car_index=args.ref_car_index, cache_dir=args.acc_cache)
        ref.label = rl
    out = run_analysis(me, ref)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    summary = {"out": str(args.out), "ok": out.get("ok"), "track": out.get("track"),
               "corner_loss_top": (out.get("corner_loss_best_vs_ref") or [])[:5],
               "theoretical": {k: v["theoretical"] for k, v in (out.get("drivers") or {}).items()}}
    print(json.dumps(summary, indent=1, default=float))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="vre-rpy",
        description=(
            "Convert Assetto Corsa Competizione .rpy replays into AI-labeled "
            "lap data and a MoTeC i2 Pro workspace (multi-lap beacons, track map, "
            "Main vs fastest-valid Ref)."
        ),
    )
    sub = ap.add_subparsers(dest="cmd")

    # Default export (also works as: vre-rpy --rpy ... --out-dir ...)
    ex = sub.add_parser("export", help="Export .rpy → Motec workspace + AI pack")
    ex.add_argument("--rpy", action="append", required=True, help="PATH or PATH:Label")
    ex.add_argument("--out-dir", type=Path, required=True)
    ex.add_argument("--car-index", type=int, default=None, help="car in a race replay (default: your car)")
    ex.add_argument("--electronics", type=Path, default=None)
    ex.set_defaults(func=cmd_export)

    vf = sub.add_parser(
        "verify",
        help="Evidence gate: .ldx lap times vs .rpy, Internal Maths SPEED, venue .mt2",
    )
    vf.add_argument("--motec-dir", type=Path, required=True)
    vf.add_argument(
        "--driver",
        action="append",
        required=True,
        help="RPY:LD:Label (repeat)",
    )
    vf.add_argument("--workspace", default="VRE_Export")
    vf.add_argument("--mt2", default=None, help="default: <.ld Venue>.mt2")
    vf.add_argument("--mt2-source", type=Path, default=None)
    vf.set_defaults(func=cmd_verify)

    fr = sub.add_parser(
        "frames",
        help="Full-rate per-lap frames (position, wheels, synced inputs) for frame-by-frame review",
    )
    fr.add_argument("--rpy", action="append", required=True, help="PATH or PATH:Label")
    fr.add_argument("--out-dir", type=Path, required=True)
    fr.add_argument("--laps", default="all", help="all | best | comma-separated lap indices")
    fr.add_argument("--acc-cache", type=Path, default=None, help="ACC Content/Cache (or env VRE_ACC_CACHE)")
    fr.set_defaults(func=cmd_frames)

    lm = sub.add_parser("limits", help="Track-limit report: cut attribution, excursions, legal track use")
    lm.add_argument("--rpy", action="append", required=True, help="PATH or PATH:Label (same track; more = better)")
    lm.add_argument("--out", type=Path, required=True, help="report JSON")
    lm.add_argument("--zone-model", type=Path, default=None, help="per-track zone model JSON to load/update")
    lm.add_argument("--car-index", type=int, default=None)
    lm.add_argument("--acc-cache", type=Path, default=None)
    lm.set_defaults(func=cmd_limits)

    tp = sub.add_parser("track-pack", help="Generate corner/terrain packs from your ACC install's AI lines")
    tp.add_argument("--track", action="append", help="Cache folder name (repeat); default: every track")
    tp.add_argument("--out-dir", type=Path, required=True)
    tp.add_argument("--acc-cache", type=Path, default=None)
    tp.set_defaults(func=cmd_track_pack)

    an = sub.add_parser("analyze", help="Corner-by-corner analysis vs a reference (events, losses, car state)")
    an.add_argument("--rpy", required=True, help="your replay PATH or PATH:Label")
    an.add_argument("--ref", default=None, help="reference replay PATH or PATH:Label")
    an.add_argument("--out", type=Path, required=True)
    an.add_argument("--car-index", type=int, default=None)
    an.add_argument("--ref-car-index", type=int, default=None, help="car in the reference replay (race replays)")
    an.add_argument("--acc-cache", type=Path, default=None)
    an.set_defaults(func=cmd_analyze)

    # Back-compat: `vre-rpy --rpy X --out-dir Y` (no subcommand)
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] not in ("export", "verify", "frames", "limits", "track-pack", "analyze", "-h", "--help") and argv[0].startswith("-"):
        argv = ["export", *argv]

    args = ap.parse_args(argv)
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
