#!/usr/bin/env python3
"""ACC .rpy parser — metadata + future telemetry hooks.

RE spike result (see docs/superpowers/specs/acc-rpy-spike-notes.md):
  NO-GO for ≥20 Hz sample extraction from .rpy in this milestone.
  Filename + cleartext islands still yield useful session metadata.
  Telemetry product path: SM logger / MoTeC via acc_ingest.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Inputs + wheel/temps Motec-locked via FUN_140fdf850 (arr_30 + arr_48).
RPY_TELEMETRY_GO = True
RPY_TELEMETRY_GO_REASON = (
    "FUN_140fdf850 arr_30/arr_48: throttle/brake/steer/gear/speed/rpm/g_lat/g_lon, "
    "wheel_angular×4, disc_temp×4, tyre_load×4, slip_ratio×4, wheel_dirt×4, time. "
    "Motec |r| thr≈0.77 brk≈0.86 spd≈0.99 steer≈0.78 gear≈0.96 rpm≈0.81 "
    "g_lat≈0.93 g_lon≈0.85 wheel≈0.98 brake_temp≈0.97. "
    "Still open: tyre_press/tair, sus_mm, TC/ABS InAction (not in FUN_140fdf850). "
    "nd_slip+marbles file_go. Decode: arr30_decode."
)

_SESSION_MAP = {
    "P": "practice",
    "Q": "qualifying",
    "R": "race",
    "O": "hotlap",
}

_KNOWN_CARS = (
    "ferrari_296_gt3",
    "ferrari_488_gt3_evo",
    "audi_r8_lms_evo_ii",
    "mclaren_720s_gt3_evo",
    "porsche_991ii_gt3_r",
    "lamborghini_huracan_gt3_evo2",
    "bmw_m4_gt3",
    "mercedes_amg_gt3_evo",
)


def extract_filename_hints(name: str) -> Dict[str, Optional[str]]:
    base = Path(name).stem
    session_type = None
    car = None
    track = None
    date = None
    time_s = None
    m = re.match(
        r"^ACC(?:Replay)?_(\d{6})-(\d{6})_([A-Z])_(.+)$",
        base,
        re.IGNORECASE,
    )
    if m:
        date, time_s = m.group(1), m.group(2)
        session_type = _SESSION_MAP.get(m.group(3).upper(), m.group(3))
        rest = re.sub(r"_(mp|highlights|ALIENLAP)$", "", m.group(4), flags=re.I)
        for car_id in _KNOWN_CARS:
            if rest.lower().startswith(car_id):
                car = car_id
                track = rest[len(car_id) :].lstrip("_") or None
                break
        if car is None:
            # fallback: last underscore token = track
            parts = rest.rsplit("_", 1)
            if len(parts) == 2:
                car, track = parts[0], parts[1]
            else:
                track = rest
    return {
        "session_type": session_type,
        "car": car,
        "track": track,
        "date_yymmdd": date,
        "time_hhmmss": time_s,
        "stem": base,
    }


def _read_ascii_at(data: bytes, off: int, max_len: int = 64) -> str:
    end = off
    while end < len(data) and end - off < max_len and 32 <= data[end] < 127:
        end += 1
    return data[off:end].decode("ascii", errors="replace")


def parse_length_prefixed_ascii(data: bytes, start: int, end: int) -> List[Dict[str, Any]]:
    """Scan [start,end) for int32-length + ASCII payloads (UE/Kunos style)."""
    fields: List[Dict[str, Any]] = []
    i = start
    while i + 4 <= end:
        n = struct.unpack_from("<i", data, i)[0]
        if 1 <= n <= 128 and i + 4 + n <= len(data):
            chunk = data[i + 4 : i + 4 + n]
            if all(32 <= b < 127 for b in chunk):
                fields.append({"offset": i, "len": n, "value": chunk.decode("ascii")})
                i += 4 + n
                continue
        i += 1
        if len(fields) >= 24:
            break
    return fields


def parse_header_islands(data: bytes) -> Dict[str, Any]:
    """Pull cleartext metadata islands from the replay header."""
    ver = list(struct.unpack_from("<HH", data, 0)) if len(data) >= 4 else []
    out: Dict[str, Any] = {
        "header_u32": [struct.unpack_from("<I", data, i)[0] for i in range(0, min(64, len(data)), 4)],
        "format_u16": ver,
        "format_fingerprint": data[:8].hex() if len(data) >= 8 else data.hex(),
        "driver_nicks": [],
        "steam_ids": [],
        "nations": [],
        "server_name": None,
        "len_prefixed_fields": [],
    }
    # SP driver block is near 260; MP shifts later — scan first 4KB
    fields = parse_length_prefixed_ascii(data, 200, min(len(data), 4096))
    out["len_prefixed_fields"] = fields[:20]
    for f in fields:
        v = f["value"]
        if v.startswith("S7656"):
            out["steam_ids"].append(v)
        elif len(v) == 3 and v.isalpha():
            out["nations"].append(v)
        elif 3 <= len(v) <= 32 and re.match(r"^[A-Za-z0-9_\[\]\-\. ]+$", v):
            out["driver_nicks"].append(v.strip())
    # Steam IDs anywhere in first 8KB
    for m in re.finditer(rb"S7656\d{10,}", data[:8192]):
        sid = m.group().decode()
        if sid not in out["steam_ids"]:
            out["steam_ids"].append(sid)
    # MP server banner often early ascii long string
    for m in re.finditer(rb"[ -~]{40,200}", data[:4096]):
        s = m.group().decode("ascii", errors="replace")
        if "|" in s or "Racing" in s or "Discord" in s:
            out["server_name"] = s
            break
    # dedupe
    def _dedupe(xs: List[str]) -> List[str]:
        seen = set()
        out_l = []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out_l.append(x)
        return out_l

    out["driver_nicks"] = _dedupe(out["driver_nicks"])
    out["steam_ids"] = _dedupe(out["steam_ids"])
    out["nations"] = _dedupe(out["nations"])
    return out


def find_plausible_lap_times_ms(data: bytes, limit: int = 2_000_000) -> List[int]:
    """Deprecated heuristic int32 scan — prefer extract_lap_times_ms."""
    hits: List[int] = []
    end = min(len(data), limit) - 4
    for off in range(0, end, 4):
        (v,) = struct.unpack_from("<I", data, off)
        if 80_000 <= v <= 180_000:  # ~1:20–3:00
            hits.append(v)
    seen = set()
    out = []
    for v in hits:
        if v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= 32:
            break
    return out


def _is_pow2_time_artifact(v: int) -> bool:
    """Reject values near 2^n (e.g. 131072 == 2:11.072) misread as lap ms."""
    if v <= 0:
        return True
    for e in range(16, 22):
        if abs(v - (1 << e)) <= 16:
            return True
    return False


def extract_lap_times_ms(
    data: bytes,
    *,
    stride: int = 24,
    min_climb_samples: int = 200,
    min_lap_ms: int = 60_000,
    max_lap_ms: int = 600_000,
) -> Dict[str, Any]:
    """Extract completed lap times from the current-lap timer stream in .rpy.

    ACC stamps a ticking current-lap clock (u32 milliseconds) into a dense
    secondary stream ( empirically stride 24). At each S/F the value resets;
    the value immediately before the drop is the completed lap time.

    Validated against Spa MoTeC TIME resets on
    ``ACC_261118-203124_O_ferrari_296_gt3_spa.rpy`` (Δ ≤ 7 ms per flying lap).
    """
    best_laps: List[int] = []
    best_meta: Dict[str, Any] = {"align": None, "flying": 0, "n": 0}

    for align in range(stride):
        climb = 0
        prev: Optional[int] = None
        laps: List[int] = []
        for i in range(align, len(data) - 4, stride):
            (cur,) = struct.unpack_from("<I", data, i)
            if prev is None:
                prev = cur
                climb = 1
                continue
            if 0 <= cur - prev <= 80:
                climb += 1
                prev = cur
                continue
            if min_lap_ms <= prev <= max_lap_ms and cur + 1000 < prev:
                if climb >= min_climb_samples and not _is_pow2_time_artifact(prev):
                    laps.append(prev)
                climb = 1 if cur < 400_000 else 0
                prev = cur
                continue
            if cur > 5_000_000 or prev > 5_000_000:
                climb = 0
            else:
                climb = 1
            prev = cur

        flying = sum(1 for lap in laps if 120_000 <= lap <= 180_000)
        if flying > int(best_meta["flying"]) or (
            flying == int(best_meta["flying"]) and len(laps) > len(best_laps)
        ):
            best_laps = laps
            best_meta = {"align": align, "flying": flying, "n": len(laps)}

    # Collapse near-duplicate S/F double-samples (keep max = most complete).
    clustered: List[int] = []
    if best_laps:
        group = [best_laps[0]]
        for v in best_laps[1:]:
            if abs(v - group[-1]) <= 50:
                group.append(v)
            else:
                clustered.append(max(group))
                group = [v]
        clustered.append(max(group))

    flying_laps = [lap for lap in clustered if 130_000 <= lap <= 155_000]
    return {
        "lap_times_ms": clustered,
        "flying_lap_times_ms": flying_laps,
        "best_flying_lap_ms": min(flying_laps) if flying_laps else None,
        "stream": best_meta,
    }


def parse_rpy_metadata(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    data = path.read_bytes()
    hints = extract_filename_hints(path.name)
    header = parse_header_islands(data)
    laps = extract_lap_times_ms(data)
    return {
        "path": str(path),
        "size": len(data),
        "telemetry_go": RPY_TELEMETRY_GO,
        "telemetry_go_reason": RPY_TELEMETRY_GO_REASON,
        "filename": hints,
        "header": header,
        "lap_times_ms": laps["lap_times_ms"],
        "flying_lap_times_ms": laps["flying_lap_times_ms"],
        "best_flying_lap_ms": laps["best_flying_lap_ms"],
        "lap_time_stream": laps["stream"],
        # kept for older callers; prefer lap_times_ms
        "plausible_lap_times_ms": laps["flying_lap_times_ms"]
        or find_plausible_lap_times_ms(data),
        "samples": [],  # NO-GO: empty until RE unlocks body
    }


def rpy_to_session(
    path: str | Path,
    sessions_root: str | Path | None = None,
    *,
    allow_empty_laps: bool = True,
) -> Dict[str, Any]:
    """Write a metadata-only ACC session from .rpy (no telemetry samples).

    Returns dict with session_dir and metadata. Raises if telemetry required
    and RPY_TELEMETRY_GO is False unless allow_empty_laps=True.
    """
    from vre_rpy.normalize import (
        append_session_record,
        new_session_dir,
        write_session_start,
    )

    meta = parse_rpy_metadata(path)
    if not RPY_TELEMETRY_GO and not allow_empty_laps:
        raise RuntimeError(RPY_TELEMETRY_GO_REASON)

    track = meta["filename"].get("track") or "unknown"
    car = meta["filename"].get("car") or "unknown"
    session_dir, session_id = new_session_dir(
        str(sessions_root) if sessions_root else None,
        track,
        car,
    )
    write_session_start(
        session_dir,
        track=track,
        car=car,
        source="acc_rpy",
        phase="LEARNING",
        extras={
            "session_type": meta["filename"].get("session_type"),
            "rpy_path": meta["path"],
            "rpy_telemetry_go": RPY_TELEMETRY_GO,
            "driver_nicks": meta["header"].get("driver_nicks"),
            "format_fingerprint": meta["header"].get("format_fingerprint"),
        },
    )
    append_session_record(
        session_dir,
        {
            "record_type": "session_end",
            "note": "metadata-only; telemetry from .rpy NO-GO — use SM/MoTeC",
            "lap_times_ms": meta.get("lap_times_ms", [])[:32],
            "flying_lap_times_ms": meta.get("flying_lap_times_ms", [])[:32],
            "best_flying_lap_ms": meta.get("best_flying_lap_ms"),
            "plausible_lap_times_ms": meta["plausible_lap_times_ms"][:16],
        },
    )
    meta_path = Path(session_dir) / "rpy_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return {"session_dir": session_dir, "session_id": session_id, "metadata": meta}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Parse ACC .rpy metadata")
    ap.add_argument("rpy", type=Path, help="Path to .rpy")
    ap.add_argument("--session-out", type=Path, help="Write metadata-only VRE session here (root)")
    ap.add_argument("--json", action="store_true", help="Print full JSON metadata")
    args = ap.parse_args(argv)

    meta = parse_rpy_metadata(args.rpy)
    if args.json:
        print(json.dumps(meta, indent=2))
    else:
        print("telemetry_go:", meta["telemetry_go"], "-", meta["telemetry_go_reason"][:80], "...")
        print("filename:", meta["filename"])
        print("nicks:", meta["header"].get("driver_nicks"))
        print("steam:", meta["header"].get("steam_ids")[:3])
        flying = meta.get("flying_lap_times_ms") or []
        best = meta.get("best_flying_lap_ms")
        if flying:
            def _fmt(ms: int) -> str:
                s = ms / 1000.0
                return "%d:%06.3f" % (int(s // 60), s % 60)

            print("flying laps:", ", ".join(_fmt(x) for x in flying))
            if best is not None:
                print("best flying:", _fmt(best), "(%d ms)" % best)
        else:
            print("plausible laps ms:", meta["plausible_lap_times_ms"][:8])
    if args.session_out:
        result = rpy_to_session(args.rpy, sessions_root=args.session_out)
        print("session:", result["session_dir"])
    return 0 if not RPY_TELEMETRY_GO else 0


if __name__ == "__main__":
    # Allow running as script without package install
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    raise SystemExit(main())
