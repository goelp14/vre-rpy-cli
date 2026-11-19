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


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="vre-rpy",
        description=(
            "Convert Assetto Corsa Competizione .rpy replays into AI-labeled "
            "lap data and a MoTeC i2 Pro workspace (multi-lap beacons, track map, "
            "Main vs fastest-valid Ref)."
        ),
    )
    ap.add_argument(
        "--rpy",
        action="append",
        required=True,
        help="PATH or PATH:Label (repeat for compare / overlay)",
    )
    ap.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    ap.add_argument("--car-index", type=int, default=0)
    ap.add_argument(
        "--electronics",
        type=Path,
        default=None,
        help='Optional JSON e.g. {"TC1":7,"TC2":5,"ABS":2,"EngineMap":1}',
    )
    args = ap.parse_args(argv)

    from vre_rpy.export_rpy_analysis import run

    elec: Optional[Dict[str, Any]] = None
    if args.electronics is not None:
        elec = json.loads(Path(args.electronics).read_text(encoding="utf-8"))
    specs = [_parse_rpy_arg(s) for s in args.rpy]
    summary = run(specs, args.out_dir, car_index=args.car_index, electronics=elec)
    # Compact user-facing summary
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


if __name__ == "__main__":
    raise SystemExit(main())
