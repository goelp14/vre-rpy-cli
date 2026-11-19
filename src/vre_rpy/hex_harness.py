#!/usr/bin/env python3
"""Hex / chunk harness for ACC .rpy reverse engineering.

Usage:
  python -m vre_rpy.hex_harness path/to/file.rpy
  python -m vre_rpy.hex_harness --fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FOURCC_RE = re.compile(rb"[A-Z0-9_]{4}")


def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def read_header(path: Path, nbytes: int = 4096) -> bytes:
    with path.open("rb") as f:
        return f.read(nbytes)


def find_ascii_strings(data: bytes, min_len: int = 4) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    i = 0
    n = len(data)
    while i < n:
        if 32 <= data[i] < 127:
            j = i
            while j < n and 32 <= data[j] < 127:
                j += 1
            if j - i >= min_len:
                out.append((i, data[i:j].decode("ascii", errors="replace")))
            i = j + 1
        else:
            i += 1
    return out


def find_utf16le_strings(data: bytes, min_chars: int = 3) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    i = 0
    n = len(data) - 1
    while i < n:
        chars: List[str] = []
        j = i
        while j + 1 < len(data):
            lo, hi = data[j], data[j + 1]
            if hi == 0 and 32 <= lo < 127:
                chars.append(chr(lo))
                j += 2
            else:
                break
        if len(chars) >= min_chars:
            out.append((i, "".join(chars)))
            i = j
        else:
            i += 2 if i % 2 == 0 else 1
    return out


def find_fourcc_tags(data: bytes) -> List[Tuple[int, str]]:
    tags: List[Tuple[int, str]] = []
    for m in FOURCC_RE.finditer(data):
        # Prefer tags that look like chunk markers (followed by size-ish int)
        off = m.start()
        if off + 8 <= len(data):
            size = struct.unpack_from("<I", data, off + 4)[0]
            if 0 < size < len(data):
                tags.append((off, m.group().decode("ascii")))
    return tags


def sniff_compression(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "zlib": data[:2] in (b"\x78\x01", b"\x78\x9c", b"\x78\xda"),
        "gzip": data[:2] == b"\x1f\x8b",
        "zstd": data[:4] == b"\x28\xb5\x2f\xfd",
        "lz4": data[:4] == b"\x04\x22\x4d\x18",
        "likely_raw": True,
    }
    # entropy-ish: fraction of unique bytes in first 64k
    sample = data[:65536]
    if sample:
        info["unique_byte_ratio"] = round(len(set(sample)) / 256.0, 3)
        info["likely_raw"] = info["unique_byte_ratio"] < 0.95 and not (
            info["zlib"] or info["gzip"] or info["zstd"]
        )
    return info


def extract_filename_hints(name: str) -> Dict[str, Optional[str]]:
    """Parse ACCReplay / ACC naming: ACC_YYMMDD-HHMMSS_S_car_track.rpy"""
    base = Path(name).stem
    session_type = None
    car = None
    track = None
    # ACC_261114-212425_O_ferrari_296_gt3_spa
    m = re.match(
        r"^ACC(?:Replay)?_(\d{6})-(\d{6})_([A-Z])_(.+)$",
        base,
        re.IGNORECASE,
    )
    if m:
        session_type = {"P": "practice", "Q": "qualifying", "R": "race", "O": "hotlap"}.get(
            m.group(3).upper(), m.group(3)
        )
        rest = m.group(4)
        # strip _mp / _highlights suffixes
        rest = re.sub(r"_(mp|highlights|ALIENLAP)$", "", rest, flags=re.I)
        # known car prefix
        for car_id in (
            "ferrari_296_gt3",
            "ferrari_488_gt3_evo",
            "audi_r8_lms_evo_ii",
            "mclaren_720s_gt3_evo",
        ):
            if rest.lower().startswith(car_id):
                car = car_id
                track = rest[len(car_id) :].lstrip("_")
                break
        if car is None:
            parts = rest.rsplit("_", 1)
            if len(parts) == 2:
                car, track = parts[0], parts[1]
            else:
                track = rest
    return {
        "session_type": session_type,
        "car_hint": car,
        "track_hint": track,
        "stem": base,
    }


def scan_file(path: Path, header_bytes: int = 65536) -> Dict[str, Any]:
    data = path.read_bytes()
    header = data[:header_bytes]
    ascii_s = find_ascii_strings(header, 4)
    utf16 = find_utf16le_strings(header[:8192], 3)
    tags = find_fourcc_tags(header)
    tag_counts = Counter(t for _, t in tags)

    # Look for float sequences that look like telemetry (0..1 pedals, speed 0..350)
    pedalish = 0
    speedish = 0
    for off in range(0, min(len(header) - 4, 20000), 4):
        (v,) = struct.unpack_from("<f", header, off)
        if 0.0 <= v <= 1.0:
            pedalish += 1
        if 30.0 < v < 350.0:
            speedish += 1

    report: Dict[str, Any] = {
        "path": str(path),
        "size": len(data),
        "filename_hints": extract_filename_hints(path.name),
        "compression": sniff_compression(data),
        "header_hex_64": header[:64].hex(),
        "ascii_strings_head": ascii_s[:40],
        "utf16_strings_head": utf16[:40],
        "fourcc_top": tag_counts.most_common(20),
        "fourcc_first": tags[:30],
        "float_heuristic": {"pedalish_count": pedalish, "speedish_count": speedish},
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ACC .rpy hex/chunk harness")
    ap.add_argument("paths", nargs="*", type=Path, help=".rpy files")
    ap.add_argument("--fixtures", action="store_true", help="Scan bundled fixtures/")
    ap.add_argument("--json-out", type=Path, help="Write reports JSON")
    ap.add_argument("--header-bytes", type=int, default=65536)
    args = ap.parse_args(argv)

    paths: List[Path] = list(args.paths)
    if args.fixtures or not paths:
        paths.extend(sorted(fixtures_dir().glob("*.rpy")))
    if not paths:
        print("No .rpy files found", file=sys.stderr)
        return 1

    reports = [scan_file(p, args.header_bytes) for p in paths]
    for r in reports:
        print("=" * 72)
        print(r["path"], f"({r['size']/1e6:.1f} MB)")
        print("hints:", r["filename_hints"])
        print("compression:", r["compression"])
        print("header:", r["header_hex_64"][:80], "...")
        print("utf16:", r["utf16_strings_head"][:8])
        print("ascii:", [s for _, s in r["ascii_strings_head"][:12]])
        print("fourcc:", r["fourcc_top"][:10])
        print("floats:", r["float_heuristic"])

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(reports, indent=2))
        print("wrote", args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
