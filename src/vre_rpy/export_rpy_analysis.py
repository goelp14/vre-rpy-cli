#!/usr/bin/env python3
"""Multi-.rpy → AI agent pack + Motec analysis workbook(s).

Usage:
  python -m vre_rpy.export_rpy_analysis \\
    --rpy path/to/tomato.rpy:Tomato \\
    --rpy path/to/erik.rpy:Erik \\
    --out-dir /tmp/spa_compare

Outputs under --out-dir:
  agent_pack/          — labeled JSON streams for AI coaching
  agent_pack/<label>/streams/*/laps.json — Motec-aligned lap labels + validity
  ai_series/<label>_laps.json — same lap labels for prompt injection
  motec/<label>.ld/.ldx — per-driver Motec log + multi-lap BCN beacons
  motec/overlay.ld     — all drivers, channel names prefixed (who is who)
  motec/VRE_Export/    — Motec i2 workspace (Workbooks XRefs registered)
  motec/Open_VRE.bat   — one-click: install workspace + open Main vs Overlay
  drivers.json         — label ↔ file map (includes venue, fastest Motec lap)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_rpy_arg(spec: str) -> Tuple[Path, str]:
    """Accept PATH or PATH:Label."""
    if ":" in spec:
        # Windows drive letters: C:\... — only split on last colon if path exists
        path_part, _, label = spec.rpartition(":")
        p = Path(path_part)
        if p.exists() or path_part.endswith(".rpy"):
            return p, (label.strip() or p.stem)
    p = Path(spec)
    return p, p.stem


def _slug(s: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", s.strip())[:48]
    return s or "unnamed"


def run(
    specs: List[Tuple[Path, str]],
    out_dir: Path,
    car_index: Optional[int] = None,
    electronics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from vre_rpy.arr30_decode import decode_rpy_inputs
    from vre_rpy.env_decode import decode_weather_data
    from vre_rpy.export_agent_pack import build_pack
    from vre_rpy.lap_validity import (
        build_labeled_laps,
        decode_rpy_lap_validity,
    )
    from vre_rpy.parse_rpy import parse_rpy_metadata
    from vre_rpy.processor import extract_rpy
    from vre_rpy.rpy_motec_ld import (
        beacon_times_from_timer_laps,
        ldx_fastest_lap_label,
        motec_venue_from_track,
        write_driver_ld,
        write_overlay_ld,
    )
    from vre_rpy.setup_calcs import channel_notes, compute_setup_channels

    out_dir.mkdir(parents=True, exist_ok=True)
    pack_root = out_dir / "agent_pack"
    motec_dir = out_dir / "motec"
    motec_dir.mkdir(parents=True, exist_ok=True)

    drivers_meta: List[Dict[str, Any]] = []
    overlay_pairs: List[Tuple[str, Dict[str, Any]]] = []
    decoded_by_label: Dict[str, Dict[str, Any]] = {}
    pose_extras: Dict[str, Dict[str, Any]] = {}
    track_id_used: Optional[str] = None
    track_grip_used: Optional[float] = None

    from vre_rpy.pose_track import (
        best_segment_index,
        decode_rpy_pose_track,
        line_polyline,
        series_json as pose_series_json,
    )

    pose_dir = out_dir / "pose"
    pose_dir.mkdir(parents=True, exist_ok=True)

    for rpy_path, label in specs:
        if not rpy_path.is_file():
            raise FileNotFoundError(rpy_path)
        slug = _slug(label)
        decoded = decode_rpy_inputs(rpy_path, car_index=car_index)
        decoded_by_label[slug] = decoded
        overlay_pairs.append((slug, decoded))
        # Pose-derived channels (GPS line, lap distance, edges, slip, terrain, validity) on the
        # input clock. Optional: missing fastlane / failed sync only drops those channels.
        pose_extra = None
        pose_note = None
        try:
            from vre_rpy.motec_pose_channels import pose_channels_for_inputs
            from vre_rpy.replay_frames import ReplayFrames

            rf = ReplayFrames(rpy_path, car_index=car_index)
            if len(rf.inputs["time_s"]) == len(decoded["time_s"]):
                pose_extra = pose_channels_for_inputs(rf)
                pose_extras[slug] = pose_extra
            else:
                pose_note = "pose channels skipped: input length mismatch"
        except Exception as exc:  # pose channels are additive; never break the export
            pose_note = f"pose channels skipped: {type(exc).__name__}: {exc}"

        # Session grip from weatherData[6] (raw GO; name is hypothesis)
        ex = extract_rpy(rpy_path)
        wd = decode_weather_data(ex.weather_data or [])
        named = wd.get("named") or {}
        grip_val = named.get("track_grip_norm")
        if grip_val is None and ex.weather_data and len(ex.weather_data) > 6:
            grip_val = float(ex.weather_data[6])
        if track_grip_used is None and grip_val is not None:
            track_grip_used = float(grip_val)

        ld_path = motec_dir / f"{slug}.ld"
        file_meta = parse_rpy_metadata(rpy_path)
        pose_dec = decode_rpy_pose_track(rpy_path, car_index=car_index)
        track_id_used = track_id_used or pose_dec.get("track_id")
        venue = motec_venue_from_track(
            str(pose_dec.get("track_id") or ""),
            filename_hints=file_meta.get("filename"),
        )
        # Motec splits laps between consecutive .ldx BCN markers (ACC pattern).
        # arr24 session_end_s ≈ Motec TIME at S/F (Δ typically ≤60 ms).
        timer = decode_rpy_lap_validity(rpy_path, car_index=car_index)
        import numpy as np

        flat_t = (
            np.asarray(decoded["time_s"], dtype=np.float64)
            if decoded.get("time_s") is not None
            else None
        )
        # Motec-relative session times (TIME[0] subtracted). Do not snap to
        # index/freq — that stretches Spa flying laps (~2:19 → ~2:24). No TIME
        # channel is written into the .ld (ACC style) so Motec keeps 1e7 BCN scale.
        beacons = (
            beacon_times_from_timer_laps(
                timer.get("laps") or [],
                t_min=float(flat_t[0]),
                t_max=float(flat_t[-1]),
                motec_t0=float(flat_t[0]),
            )
            if flat_t is not None and flat_t.size >= 2
            else None
        )
        # Prefer valid flying laps only for Motec Fastest / Compare Ref.
        flying_ms = [
            int(l["lap_ms"])
            for l in (timer.get("laps") or [])
            if l.get("flying_candidate")
            and l.get("completed")
            and l.get("valid") is True
        ]
        if not flying_ms:
            flying_ms = [
                int(l["lap_ms"])
                for l in (timer.get("laps") or [])
                if l.get("flying_candidate") and l.get("completed")
            ]
        preferred_fastest = (min(flying_ms) / 1000.0) if flying_ms else None
        write_driver_ld(
            decoded,
            ld_path,
            label=slug,
            track_grip=float(grip_val) if grip_val is not None else None,
            electronics=electronics,
            venue=venue,
            beacon_times_s=beacons,
            preferred_fastest_s=preferred_fastest,
            extra=pose_extra,
        )

        labeled_laps = build_labeled_laps(
            timer.get("laps") or [],
            beacons
            if beacons
            else (
                [0.0, float(flat_t[-1] - flat_t[0])]
                if flat_t is not None and flat_t.size >= 2
                else [0.0, 1.0]
            ),
            venue=venue,
            preferred_fastest_s=preferred_fastest,
            pose_segments=pose_dec.get("lap_segments"),
            motec_t0=float(flat_t[0]) if flat_t is not None and flat_t.size else 0.0,
        )
        labeled_laps["label"] = slug
        labeled_laps["motec_ld"] = str(ld_path.relative_to(out_dir))
        labeled_laps["motec_fastest_range"] = ldx_fastest_lap_label(ld_path)

        # AI-facing setup calc series (same channels as Motec VRE_*)
        setup = compute_setup_channels(
            decoded,
            track_grip=float(grip_val) if grip_val is not None else None,
            electronics=electronics,
        )
        setup_dir = out_dir / "setup_calcs"
        setup_dir.mkdir(exist_ok=True)
        setup_json = {
            "label": slug,
            "track_grip": grip_val,
            "notes": channel_notes(),
            "channels": {
                k: [float(x) for x in v[:: max(1, len(v) // 4000)]]
                for k, v in setup.items()
            },
            "count": int(decoded["count"]),
            "downsampled": True,
            "laps": labeled_laps,
        }
        (setup_dir / f"{slug}.json").write_text(
            json.dumps(setup_json, indent=2), encoding="utf-8"
        )

        (pose_dir / f"{slug}_pose.json").write_text(
            json.dumps(
                {"label": slug, **pose_series_json(pose_dec, max_samples=8000)},
                indent=2,
            ),
            encoding="utf-8",
        )
        (pose_dir / f"{slug}_line.json").write_text(
            json.dumps(
                {"label": slug, **line_polyline(pose_dec, lap_index=best_segment_index(pose_dec) or 0, stride=3)},
                indent=2,
            ),
            encoding="utf-8",
        )

        # Per-driver agent pack subdirectory (includes streams/*/laps.json)
        pack = build_pack(rpy_path, pack_root / slug, motec_path=None, car_index=car_index)
        drivers_meta.append(
            {
                "label": slug,
                "rpy": str(rpy_path.resolve()),
                "count": int(decoded["count"]),
                "pose_count": int(pose_dec["count"]),
                "pose_channels": sorted((pose_extra or {}).get("channels", {}).keys()),
                "pose_sync": (pose_extra or {}).get("meta", {}).get("sync"),
                "pose_note": pose_note,
                "estimate_quality": {
                    "VRE_TC_EST": "precision 0.50 / recall 0.59 vs MoTeC TC (Spa gold)",
                    "VRE_ABS_EST": "precision 0.42 / recall 0.31 vs MoTeC ABS (Spa gold)",
                },
                "lap_candidates": len(pose_dec.get("lap_segments") or []),
                "venue": venue,
                "motec_beacons": len(beacons or []),
                "motec_total_laps": labeled_laps.get("total_laps"),
                "motec_fastest_lap": labeled_laps.get("fastest_label"),
                "motec_fastest_time_s": labeled_laps.get("fastest_time_s"),
                "motec_ld": str(ld_path.relative_to(out_dir)),
                "agent_pack": str((pack_root / slug).relative_to(out_dir)),
                "pose_json": str((pose_dir / f"{slug}_pose.json").relative_to(out_dir)),
                "line_json": str((pose_dir / f"{slug}_line.json").relative_to(out_dir)),
                "setup_calcs": str((setup_dir / f"{slug}.json").relative_to(out_dir)),
                "ai_laps": f"ai_series/{slug}_laps.json",
                "track_grip": grip_val,
                "gates": pack.get("gates"),
                "laps": labeled_laps,
            }
        )

    overlay_path = None
    if len(overlay_pairs) >= 1:
        overlay_path = motec_dir / "overlay.ld"
        write_overlay_ld(
            overlay_pairs,
            overlay_path,
            track_grip=track_grip_used,
            electronics=electronics,
            extras=pose_extras,
        )

    # Combined AI summary for multi-driver coaching prompts
    summary = {
        "schema": "vre.acc_rpy.analysis_export.v1",
        "drivers": drivers_meta,
        "overlay_ld": str(overlay_path.relative_to(out_dir)) if overlay_path else None,
        "channels_file_go": [
            "throttle",
            "brake",
            "steer_deg",
            "gear",
            "speed_kmh",
            "rpm",
            "g_lat",
            "g_lon",
            "wheel_angular",
            "wheel_speed_ms",
            "disc_temp",
            "tyre_load",
            "slip_ratio",
            "wheel_dirt",
            "nd_slip",
            "marbles",
            "is_on_marbles",
            "phys_478",
            "sus_proxy",
            "phys_500",
            "time_s",
            "pose_xyz",
            "spline",
            "heading_deg",
            "dist_m",
        ],
        "motec_calc_channels": sorted(
            {
                "VRE_COAST",
                "VRE_PEDAL_GAP",
                "VRE_STEER_RATE",
                "VRE_SPEED_KMH",
                "VRE_US_OS",
                "VRE_YAW_DEG",
                "VRE_SPIN",
                "VRE_LOCK",
                "VRE_TC_EST",
                "VRE_ABS_EST",
                "VRE_TC_LEVEL",
                "VRE_ABS_LEVEL",
                "VRE_TCCUT",
                "VRE_ENG_MAP",
                "VRE_RAKE_LOAD",
                "VRE_LOAD_F",
                "VRE_LOAD_R",
                "VRE_SLIP_MAX",
                "VRE_SLIP_F",
                "VRE_SLIP_R",
                "VRE_BT_BAL",
                "VRE_TRACK_GRIP",
                "VRE_LAT_UTIL",
                "VRE_G_LAT",
                "VRE_G_LON",
            }
            | set(channel_notes().keys())
        ),
        "electronics": {
            "levels_supplied": electronics,
            "split": {
                "settings": ["TC/TC1", "TCCut/TC2", "ABS", "EngineMap"],
                "state": ["TC_InAction / Motec TC", "ABS_InAction / Motec ABS"],
            },
            "file_status": "levels + InAction still NO-GO in .rpy; ACT proxies exported",
        },
        "track_grip": track_grip_used,
        "setup_calc_notes": channel_notes(),
        "motec_pace_setup": None,  # filled below
        "motec_workspace": None,  # filled below
        "still_no_go": [
            "tyre_press",
            "tyre_tair",
            "sus_travel_mm",
            "tc_level_file",
            "abs_level_file",
            "tc_in_action_file",
            "abs_in_action_file",
            "torque",
            "limiter",
            "track_limits_official",
        ],
        "note": None,  # filled after workspace write
    }

    # Track map reference (ACC Cache fastlane + spa_acc.json corners)
    track_meta = None
    if track_id_used:
        try:
            from vre_rpy.acc_fastlane import (
                parse_acc_fastlane,
                resolve_fastlane,
                to_track_geometry,
            )
            from vre_rpy.pose_track import load_track_config

            cfg = load_track_config(str(track_id_used))
            fl = parse_acc_fastlane(resolve_fastlane(str(track_id_used)))
            geom = to_track_geometry(fl, cfg)
            track_dir = out_dir / "track"
            track_dir.mkdir(exist_ok=True)
            (track_dir / "geometry.json").write_text(
                json.dumps(geom, indent=2), encoding="utf-8"
            )
            (track_dir / "config.json").write_text(
                json.dumps(cfg, indent=2), encoding="utf-8"
            )
            track_meta = {
                "track_id": cfg.get("track_id"),
                "geometry": "track/geometry.json",
                "config": "track/config.json",
                "centerline_points": fl["point_count"],
                "length_m_fastlane": fl["cumulative_length_m"],
                "limits_official": False,
            }
            summary["track"] = track_meta
        except Exception as exc:  # noqa: BLE001
            summary["track"] = {"error": str(exc), "track_id": track_id_used}

    from vre_rpy.motec_worksheets import pack_json as motec_pack
    from vre_rpy.motec_workspace import write_motec_workspace_bundle

    summary["motec_pace_setup"] = motec_pack()
    (out_dir / "motec_pace_setup.json").write_text(
        json.dumps(summary["motec_pace_setup"], indent=2), encoding="utf-8"
    )

    # Motec Main/Ref: with 2+ .rpys, Ref = driver with fastest *valid* flying lap.
    from vre_rpy.lap_validity import order_drivers_main_ref

    compare_order = order_drivers_main_ref(drivers_meta)
    ordered_meta = compare_order["drivers"]
    summary["motec_compare_order"] = {
        "ref_reason": compare_order.get("ref_reason"),
        "main_label": compare_order.get("main_label")
        or (ordered_meta[0]["label"] if ordered_meta else None),
        "ref_label": compare_order.get("ref_label"),
        "ref_valid_time_s": compare_order.get("ref_valid_time_s"),
        "order": [d["label"] for d in ordered_meta],
    }
    ws_meta = write_motec_workspace_bundle(
        motec_dir=motec_dir,
        drivers=[
            {"label": d["label"], "ld": out_dir / d["motec_ld"]} for d in ordered_meta
        ],
        overlay_ld=overlay_path,
        workspace_name="VRE_Export",
    )
    summary["motec_workspace"] = {
        **ws_meta,
        "how_to_open": "Double-click motec/Open_VRE.bat (or Open_VRE.ps1)",
        "hotkeys": {"F9": "distance axis", "F3": "time variance"},
        "compare_order": summary["motec_compare_order"],
    }
    main_lab = summary["motec_compare_order"]["main_label"]
    ref_lab = summary["motec_compare_order"]["ref_label"]
    summary["note"] = (
        "ONE-CLICK Motec: motec/Open_VRE.bat → installs Documents\\MoTeC\\i2\\Workspaces\\"
        "VRE_Export and opens VRE Compare. "
        f"Main={main_lab}, Ref={ref_lab} "
        "(Ref = fastest valid flying lap across the given .rpys). "
        "Channels are ACC-native (SPEED_KMH, THROTTLE, BRAKE, … + VRE_*). "
        "Alternate: motec/overlay.ld + workbook VRE Overlay (Label_CHANNEL prefixes). "
        "F9=distance, F3=variance. Electronics maps via --electronics until file lanes lock."
    )

    (out_dir / "drivers.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Flat AI series (downsampled) + Motec-aligned lap labels for prompt injection
    ai_dir = out_dir / "ai_series"
    ai_dir.mkdir(exist_ok=True)
    from vre_rpy.arr30_decode import series_json

    laps_by_slug = {d["label"]: d.get("laps") for d in drivers_meta}
    for slug, decoded in decoded_by_label.items():
        (ai_dir / f"{slug}.json").write_text(
            json.dumps(
                {"label": slug, **series_json(decoded, max_samples=8000)},
                indent=2,
            ),
            encoding="utf-8",
        )
        if laps_by_slug.get(slug):
            (ai_dir / f"{slug}_laps.json").write_text(
                json.dumps(laps_by_slug[slug], indent=2),
                encoding="utf-8",
            )

    return summary


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Export ACC .rpy → AI pack + Motec workbook (multi-driver overlay)"
    )
    ap.add_argument(
        "--rpy",
        action="append",
        required=True,
        help="PATH or PATH:Label (repeat for overlay)",
    )
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--car-index", type=int, default=0)
    ap.add_argument(
        "--electronics",
        type=Path,
        default=None,
        help='JSON with TC/ABS settings, e.g. {"TC1":7,"TC2":5,"ABS":2,"EngineMap":1}',
    )
    args = ap.parse_args(argv)

    elec: Optional[Dict[str, Any]] = None
    if args.electronics is not None:
        elec = json.loads(Path(args.electronics).read_text(encoding="utf-8"))

    specs = [_parse_rpy_arg(s) for s in args.rpy]
    summary = run(specs, args.out_dir, car_index=args.car_index, electronics=elec)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
