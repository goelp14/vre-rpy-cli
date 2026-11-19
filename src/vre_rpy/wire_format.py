#!/usr/bin/env python3
"""Partial ACC .rpy wire walker from AC2 exe RE (see acc-rpy-wire-from-exe.md).

Parses only the confidently ordered header prefix. Does not emit telemetry
samples and must not flip RPY_TELEMETRY_GO.
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class RpyTruncated(EOFError):
    pass


@dataclass
class HeaderPrefix:
    version: int
    blob30: bytes
    name_string: str
    # Best-effort fields after the empty/name string when layout matches Spa v25.
    u32_after_name: Optional[int] = None
    byte_a: Optional[int] = None
    byte_b: Optional[int] = None
    fe7a20_u32s: List[int] = field(default_factory=list)
    fe7a20_payload_a: bytes = b""
    fe7a20_payload_b: bytes = b""
    section_magic_ok: Optional[bool] = None
    offset_after_prefix: int = 0
    notes: List[str] = field(default_factory=list)


class _Reader:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise RpyTruncated(f"need {n} bytes at 0x{self.pos:x}, file ends 0x{len(self.data):x}")
        out = self.data[self.pos : end]
        self.pos = end
        return out

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def u8(self) -> int:
        return self.take(1)[0]

    def lp_bytes(self) -> bytes:
        n = self.u32()
        if n > 0x100000:
            raise ValueError(f"implausible length {n} at 0x{self.pos - 4:x}")
        return self.take(n)


def parse_header_prefix(data: bytes) -> HeaderPrefix:
    """Parse version + 0x30 blob + length-prefixed string (+ Spa-like trailing trio)."""
    r = _Reader(data)
    version = r.u16()
    blob30 = r.take(0x30)
    raw_name = r.lp_bytes()
    try:
        name = raw_name.decode("utf-8")
    except UnicodeDecodeError:
        name = raw_name.decode("latin-1", errors="replace")

    notes: List[str] = []
    if version < 0x16:
        notes.append(f"version {version} < 0x16: exe may skip or alter 0x30 path")

    u32_after: Optional[int] = None
    byte_a: Optional[int] = None
    byte_b: Optional[int] = None
    # Spa v25 / modern path after fe7950: u32 (ver>=0x0B), u8, u8 (ver>=0x10).
    if version >= 0x0B and r.pos + 4 <= len(data):
        u32_after = r.u32()
        if r.pos < len(data):
            byte_a = r.u8()
        if version >= 0x10 and r.pos < len(data):
            byte_b = r.u8()
        notes.append("consumed u32[+u8[+u8]] after name (open_check ver gates)")

    # FUN_140fe7a20: 13×u32 into struct, then (u32 len + bytes)×2
    fe7a20: List[int] = []
    pay_a = b""
    pay_b = b""
    magic_ok: Optional[bool] = None
    if version >= 0x10 and r.pos + 13 * 4 + 8 <= len(data):
        fe7a20 = [r.u32() for _ in range(13)]
        pay_a = r.lp_bytes()
        pay_b = r.lp_bytes()
        notes.append("consumed FUN_140fe7a20 (13×u32 + 2×lp)")
        # FUN_140fe6d80: require u32 == 0x7B section magic
        if r.pos + 4 <= len(data):
            magic = r.u32()
            magic_ok = magic == 0x7B
            notes.append(
                f"section magic A 0x{magic:X} ({'ok' if magic_ok else 'EXPECTED 0x7B'})"
            )
        # FUN_1434f1900: 7×u32 (6 reads + tail-call read)
        block_4f: List[int] = []
        if r.pos + 28 <= len(data):
            block_4f = [r.u32() for _ in range(7)]
            notes.append("consumed FUN_1434f1900 (7×u32)")
        magic_b_ok: Optional[bool] = None
        if r.pos + 4 <= len(data):
            magic_b = r.u32()
            magic_b_ok = magic_b == 0x7B
            notes.append(
                f"section magic B 0x{magic_b:X} ({'ok' if magic_b_ok else 'EXPECTED 0x7B'})"
            )
            notes.append("next: u16 count loop (fc3af0/fdb810) — driver entries")
        if magic_b_ok is not None:
            magic_ok = bool(magic_ok) and magic_b_ok
    else:
        notes.append("skipped FUN_140fe7a20 (version/size)")
        block_4f = []

    return HeaderPrefix(
        version=version,
        blob30=blob30,
        name_string=name,
        u32_after_name=u32_after,
        byte_a=byte_a,
        byte_b=byte_b,
        fe7a20_u32s=fe7a20,
        fe7a20_payload_a=pay_a,
        fe7a20_payload_b=pay_b,
        section_magic_ok=magic_ok,
        offset_after_prefix=r.pos,
        notes=notes,
    )


def header_prefix_json(path: str | Path) -> Dict[str, Any]:
    data = Path(path).read_bytes()
    h = parse_header_prefix(data)
    d = asdict(h)
    d["blob30_hex"] = h.blob30.hex()
    d["fe7a20_payload_a_hex"] = h.fe7a20_payload_a.hex()
    d["fe7a20_payload_b_hex"] = h.fe7a20_payload_b.hex()
    if h.fe7a20_u32s:
        f32s = struct.unpack(f"<{len(h.fe7a20_u32s)}f", struct.pack(f"<{len(h.fe7a20_u32s)}I", *h.fe7a20_u32s))
        d["fe7a20_f32s"] = [None if x != x else x for x in f32s]  # NaN -> null
    else:
        d["fe7a20_f32s"] = []
    d["size"] = len(data)
    del d["blob30"]
    del d["fe7a20_payload_a"]
    del d["fe7a20_payload_b"]
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rpy", type=Path)
    args = ap.parse_args()
    print(json.dumps(header_prefix_json(args.rpy), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
