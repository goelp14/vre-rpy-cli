"""Write a labeled, AI/script-ingestible pack from ACC .rpy (+ optional MoTeC).

Outputs under --out-dir:
  manifest.json          — schema, GO gates, stream catalog, per-driver labels
  stream_catalog.json    — full RE-derived channel dictionary
  session.json           — metadata + lap times + env (GO fields only claimed)
  pose/<label>.csv       — rec_68 XYZ (file_hyp)
  streams/<label>/...    — per-stream JSON when samples exist
  motec_analysis.ld      — optional; written when --motec is given
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _slug(s: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", s.strip())[:80]
    return s or "unnamed"


def _ensure_ldparser_path() -> None:
    """gotzl/ldparser is not on PyPI; allow a local clone on PYTHONPATH."""
    try:
        import ldparser  # noqa: F401

        return
    except ImportError:
        pass
    for cand in (
        Path.home() / "tools" / "ldparser",
        Path("/home/pranav/tools/ldparser"),
    ):
        if (cand / "ldparser.py").is_file() or (cand / "ldparser").is_dir():
            p = str(cand)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


def _label_from_rpy(path: Path, meta: Dict[str, Any], index: int) -> str:
    # Prefer driver nick from extract if present
    drivers = meta.get("cars_info") or meta.get("drivers") or []
    nick = None
    if isinstance(drivers, list) and index < len(drivers):
        d = drivers[index]
        if isinstance(d, dict):
            nick = d.get("nick") or d.get("name") or d.get("driver")
            if isinstance(nick, list):
                nick = nick[0] if nick else None
    if not nick:
        nick = path.stem
    return _slug(f"{index:02d}_{nick}")


def build_pack(
    rpy_path: Path,
    out_dir: Path,
    motec_path: Optional[Path] = None,
    car_index: int = 0,
) -> Dict[str, Any]:
    from vre_rpy.stream_catalog import catalog_json
    from vre_rpy.processor import (
        extract_rpy,
        extract_to_dict,
        iter_pose_samples,
        write_pose_csv,
    )
    from vre_rpy.parse_rpy import (
        RPY_TELEMETRY_GO,
        RPY_TELEMETRY_GO_REASON,
        extract_lap_times_ms,
        parse_rpy_metadata,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    catalog = catalog_json()
    (out_dir / "stream_catalog.json").write_text(
        json.dumps(catalog, indent=2), encoding="utf-8"
    )

    ex = extract_rpy(rpy_path)
    meta = extract_to_dict(ex, include_raw=False)
    file_meta = parse_rpy_metadata(rpy_path)
    laps = extract_lap_times_ms(rpy_path.read_bytes())

    label = _label_from_rpy(rpy_path, meta, car_index)
    pose_dir = out_dir / "pose"
    streams_dir = out_dir / "streams" / label
    streams_dir.mkdir(parents=True, exist_ok=True)

    pose_csv = pose_dir / f"{label}.csv"
    n_pose = write_pose_csv(ex, pose_csv, car_index=car_index)

    # Lap stream (file_go) — times only
    lap_payload = {
        "id": "lap_time_ms",
        "status": "file_go",
        "label": label,
        "lap_times_ms": laps.get("lap_times_ms", []),
        "flying_lap_times_ms": laps.get("flying_lap_times_ms", []),
        "best_flying_lap_ms": laps.get("best_flying_lap_ms"),
        "stream": laps.get("stream"),
    }
    (streams_dir / "lap_time_ms.json").write_text(
        json.dumps(lap_payload, indent=2), encoding="utf-8"
    )

    # Motec-aligned labeled laps (same BCN times as .ldx) for AI coaching
    try:
        from vre_rpy.lap_validity import (
            build_labeled_laps,
            decode_rpy_lap_validity,
        )
        from vre_rpy.pose_track import decode_rpy_pose_track
        from vre_rpy.rpy_motec_ld import (
            beacon_times_from_timer_laps,
            motec_venue_from_track,
        )

        timer = decode_rpy_lap_validity(rpy_path, car_index=car_index)
        timer_laps = timer.get("laps") or []
        t_min = min((float(l["session_start_s"]) for l in timer_laps), default=0.0)
        t_max = max((float(l["session_end_s"]) for l in timer_laps), default=t_min)
        beacons = beacon_times_from_timer_laps(
            timer_laps,
            t_min=t_min,
            t_max=max(t_max, t_min + 1e-3),
            motec_t0=t_min,
        )
        preferred = (
            float(laps["best_flying_lap_ms"]) / 1000.0
            if laps.get("best_flying_lap_ms")
            else None
        )
        pose_segs = None
        track_id = ""
        try:
            pose_full = decode_rpy_pose_track(rpy_path, car_index=car_index)
            pose_segs = pose_full.get("lap_segments")
            track_id = str(pose_full.get("track_id") or "")
        except Exception:
            pass
        venue = motec_venue_from_track(
            track_id,
            filename_hints={
                **(file_meta.get("filename") or {}),
                "name": rpy_path.stem,
            },
        )
        labeled = build_labeled_laps(
            timer_laps,
            beacons,
            venue=venue,
            preferred_fastest_s=preferred,
            pose_segments=pose_segs,
            motec_t0=t_min,
        )
        labeled["label"] = label
        (streams_dir / "laps.json").write_text(
            json.dumps(labeled, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        (streams_dir / "laps.json").write_text(
            json.dumps(
                {"id": "laps", "status": "error", "error": str(exc), "label": label},
                indent=2,
            ),
            encoding="utf-8",
        )

    # Weather (file_go raw)
    weather = {
        "id": "weather_data",
        "status": "file_go",
        "weather_data": meta.get("weather_data"),
        "env": meta.get("env"),
    }
    (streams_dir / "weather_data.json").write_text(
        json.dumps(weather, indent=2), encoding="utf-8"
    )

    # arr_30 throttle / brake / speed (file_go)
    try:
        from .arr30_decode import decode_arr30, find_arr, series_json

        a30 = find_arr(ex, 0x30, car_index)
        a48 = find_arr(ex, 0x48, car_index)
        if a30 is not None:
            decoded = decode_arr30(a30, a48)
            pedals = {
                "id": "arr30_inputs",
                "status": "file_go",
                "label": label,
                **series_json(decoded, max_samples=20000),
            }
            (streams_dir / "arr30_inputs.json").write_text(
                json.dumps(pedals, indent=2), encoding="utf-8"
            )
    except Exception as exc:  # noqa: BLE001
        (streams_dir / "arr30_inputs.json").write_text(
            json.dumps({"id": "arr30_inputs", "status": "error", "error": str(exc)}, indent=2),
            encoding="utf-8",
        )

    # Pose + spline (file_go)
    try:
        from .pose_track import decode_rpy_pose_track, series_json as pose_series

        pose_dec = decode_rpy_pose_track(rpy_path, car_index=car_index)
        pose_json = {
            "id": "pose_track",
            "status": "file_go",
            "csv": str(pose_csv.relative_to(out_dir)),
            **pose_series(pose_dec, max_samples=5000),
        }
    except Exception as exc:  # noqa: BLE001
        poses = iter_pose_samples(ex, car_index)
        pose_json = {
            "id": "pose_rec68",
            "status": "error",
            "error": str(exc),
            "count": len(poses),
            "csv": str(pose_csv.relative_to(out_dir)),
            "head": poses[:3],
            "tail": poses[-3:] if len(poses) > 3 else poses,
        }
    (streams_dir / "pose_track.json").write_text(
        json.dumps(pose_json, indent=2), encoding="utf-8"
    )

    # Runtime-mapped but file NO-GO stubs (so agents know names + chase state)
    pending = []
    for s in catalog["streams"]:
        if s.get("file_status") == "no_go" or (
            s["status"] == "runtime_go" and s.get("file_status", "no_go") != "file_go"
        ):
            pending.append(
                {
                    "id": s["id"],
                    "label": s["label"],
                    "status": "no_go",
                    "runtime_map": s.get("runtime_map"),
                    "ue_property": s.get("ue_property") or s.get("source"),
                    "reason": "runtime offset known; .rpy packing not Motec-locked",
                }
            )
    (streams_dir / "pending_runtime_streams.json").write_text(
        json.dumps({"streams": pending}, indent=2), encoding="utf-8"
    )

    motec_out = None
    if motec_path and motec_path.is_file():
        try:
            _ensure_ldparser_path()
            from vre_rpy.motec_analysis_ld import write_analysis_ld

            motec_out = out_dir / "motec_analysis.ld"
            write_analysis_ld(str(motec_path), str(motec_out))
        except Exception as exc:  # noqa: BLE001 — surface in manifest
            motec_out = {"error": str(exc), "src": str(motec_path)}

    laps_summary = None
    laps_path = streams_dir / "laps.json"
    if laps_path.is_file():
        try:
            laps_summary = json.loads(laps_path.read_text(encoding="utf-8"))
        except Exception:
            laps_summary = None

    session = {
        "schema": "vre.acc_rpy.session.v1",
        "rpy": str(rpy_path),
        "label": label,
        "file_meta": file_meta,
        "version": meta.get("version"),
        "session_name": meta.get("session_name"),
        "player_car_model": meta.get("player_car_model"),
        "best_flying_lap_ms": laps.get("best_flying_lap_ms"),
        "flying_lap_times_ms": laps.get("flying_lap_times_ms"),
        "venue": (laps_summary or {}).get("venue"),
        "motec_total_laps": (laps_summary or {}).get("total_laps"),
        "motec_fastest_lap": (laps_summary or {}).get("fastest_label"),
        "motec_fastest_time_s": (laps_summary or {}).get("fastest_time_s"),
        "RPY_TELEMETRY_GO": RPY_TELEMETRY_GO,
        "RPY_TELEMETRY_GO_REASON": RPY_TELEMETRY_GO_REASON,
        "pose_samples": n_pose,
    }
    (out_dir / "session.json").write_text(json.dumps(session, indent=2), encoding="utf-8")

    manifest = {
        "schema": "vre.acc_rpy.agent_pack.v1",
        "outputs": {
            "session": "session.json",
            "stream_catalog": "stream_catalog.json",
            "pose_csv": str(pose_csv.relative_to(out_dir)),
            "streams_dir": str(streams_dir.relative_to(out_dir)),
            "laps": str((streams_dir / "laps.json").relative_to(out_dir)),
            "motec_analysis_ld": (
                str(motec_out.relative_to(out_dir))
                if isinstance(motec_out, Path)
                else motec_out
            ),
        },
        "gates": {
            "RPY_TELEMETRY_GO": RPY_TELEMETRY_GO,
            "file_go_stream_ids": catalog["by_status"].get("file_go", []),
            "runtime_go_stream_ids": catalog["by_status"].get("runtime_go", []),
        },
        "drivers": [{"index": car_index, "label": label}],
        "ingest_hint": (
            "Read manifest.json then stream_catalog.json. Only use streams with "
            "status file_go for coaching claims. runtime_go entries document exe "
            "offsets; samples appear under streams/ when file_status flips to file_go."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_multi(
    rpy_paths: List[Path],
    out_dir: Path,
    motec_by_stem: Optional[Dict[str, Path]] = None,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    children = []
    for i, rpy in enumerate(rpy_paths):
        sub = out_dir / _slug(rpy.stem)
        motec = None
        if motec_by_stem:
            motec = motec_by_stem.get(rpy.stem) or motec_by_stem.get(rpy.name)
        man = build_pack(rpy, sub, motec_path=motec, car_index=0)
        children.append({"rpy": str(rpy), "dir": str(sub.relative_to(out_dir)), "manifest": man})
    root = {
        "schema": "vre.acc_rpy.agent_pack_bundle.v1",
        "count": len(children),
        "packs": children,
    }
    (out_dir / "bundle_manifest.json").write_text(json.dumps(root, indent=2), encoding="utf-8")
    return root


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("rpy", nargs="+", type=Path, help="One or more .rpy files")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--motec", type=Path, default=None, help="MoTeC .ld for single-rpy pack")
    p.add_argument(
        "--motec-map",
        type=Path,
        default=None,
        help="JSON {rpy_stem: ld_path} for multi-rpy Motec pairing",
    )
    args = p.parse_args(argv)

    motec_map = None
    if args.motec_map and args.motec_map.is_file():
        raw = json.loads(args.motec_map.read_text(encoding="utf-8"))
        motec_map = {k: Path(v) for k, v in raw.items()}

    if len(args.rpy) == 1:
        man = build_pack(args.rpy[0], args.out_dir, motec_path=args.motec)
        print(json.dumps(man, indent=2))
    else:
        man = build_multi(args.rpy, args.out_dir, motec_by_stem=motec_map)
        print(json.dumps(man, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
