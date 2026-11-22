#!/usr/bin/env python3
"""ACC .rpy structural extractor (exe-backed wire walk).

Extracts session metadata, drivers, cars, and every typed/opaque binary array
the loaders deserialize. Channel meaning for pose/physics arrays is still being
MoTeC-validated — RPY_TELEMETRY_GO stays false — but the bytes are recovered
for analysis when only a faster car's .rpy is available.

Supports modern Saved/Highlights files (tested path: format version >= 24).
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SECTION_MAGIC = 0x7B


class RpyError(Exception):
    pass


class RpyTruncated(RpyError, EOFError):
    pass


class RpyMagicError(RpyError):
    pass


@dataclass
class ArrayBlob:
    name: str
    count: int
    stride: int
    offset: int
    data: bytes = field(repr=False)

    @property
    def nbytes(self) -> int:
        return len(self.data)

    def as_f32_matrix(self) -> Optional[List[List[float]]]:
        if self.stride % 4 or self.nbytes != self.count * self.stride:
            return None
        cols = self.stride // 4
        out: List[List[float]] = []
        for i in range(self.count):
            base = i * self.stride
            out.append(list(struct.unpack_from(f"<{cols}f", self.data, base)))
        return out

    def summary(self) -> Dict[str, Any]:
        s: Dict[str, Any] = {
            "name": self.name,
            "count": self.count,
            "stride": self.stride,
            "offset": self.offset,
            "nbytes": self.nbytes,
        }
        if self.count and self.stride >= 4 and self.stride % 4 == 0:
            # first-row float peek for analysis
            n = min(8, self.stride // 4)
            s["first_row_f32"] = list(struct.unpack_from(f"<{n}f", self.data, 0))
        return s


@dataclass
class DriverInfo:
    strings: List[str]
    fields: Dict[str, Any]
    offset: int


@dataclass
class CarInfoEntry:
    car_key: int
    drivers: List[DriverInfo]


@dataclass
class SnapshotEntry:
    snap_id: int
    flag: int
    arrays: List[ArrayBlob]
    offset: int


@dataclass
class CarRecord:
    index: int
    header_18: bytes
    header_30: bytes
    sub_blobs: List[bytes]
    snapshots: List[SnapshotEntry]
    bulk_16: Optional[ArrayBlob]
    arrays: List[ArrayBlob]
    offset: int
    end_offset: int


@dataclass
class RpyExtract:
    path: str
    size: int
    version: int
    track_id_check: Optional[int]
    session_name: str
    player_car_model: Optional[int]
    setup_u32s: List[int]
    setup_f32s: List[Optional[float]]
    weather_data: List[float]
    weather_status: List[Dict[str, Any]]
    cars_info: List[CarInfoEntry]
    driver_info_30: List[bytes]
    driver_info_20: List[bytes]
    opaque_blobs: List[ArrayBlob]
    car_slot_count: int
    car_records: List[CarRecord]
    parse_notes: List[str] = field(default_factory=list)
    remaining_bytes: int = 0
    end_offset: int = 0


class _R:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise RpyTruncated(f"need {n} at 0x{self.pos:x}, have {self.remaining()}")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "little")

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def lp_str(self) -> str:
        n = self.u32()
        if n > 0x100000:
            raise RpyError(f"bad strlen {n} at 0x{self.pos - 4:x}")
        raw = self.take(n)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")

    def section_magic(self, name: str) -> None:
        off = self.pos
        mag = self.u32()
        if mag != SECTION_MAGIC:
            raise RpyMagicError(f"{name}: expected 0x7B at 0x{off:x}, got 0x{mag:X}")

    def counted(self, stride: int, name: str) -> ArrayBlob:
        off = self.pos
        n = self.u32()
        if n == 0:
            return ArrayBlob(name=name, count=0, stride=stride, offset=off, data=b"")
        if n > 50_000_000:
            raise RpyError(f"{name}: implausible count {n} at 0x{off:x}")
        data = self.take(n * stride)
        return ArrayBlob(name=name, count=n, stride=stride, offset=off, data=data)

    def opaque_bytes(self, name: str) -> ArrayBlob:
        """packer: u32 nbytes + bytes."""
        off = self.pos
        n = self.u32()
        data = self.take(n) if n else b""
        return ArrayBlob(name=name, count=n, stride=1, offset=off, data=data)


def _f32s_from_u32s(vals: List[int]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for v in vals:
        f = struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
        out.append(None if f != f else f)
    return out


def _read_weather_status_entry(r: _R, ver: int) -> Dict[str, Any]:
    """packer body."""
    names = [r.lp_str(), r.lp_str(), r.lp_str()]
    fields: Dict[str, Any] = {
        "u32_30": r.u32(),
        "u8_34": r.u8(),
        "u32_38": r.u32(),
        "u8_3c": r.u8(),
        "u32_40": r.u32(),
        "u32_44": r.u32(),
        "u32_48": r.u32(),
        "u8_4c": r.u8(),
        "u8_4d": r.u8(),
        "u8_4e": r.u8(),
        "u32_54": r.u32(),
        "u32_58": r.u32(),
        "u8_5c": r.u8(),
        "u8_5d": r.u8(),
        "u16_80": r.u16(),
        "u16_c8": r.u16(),
        "u8_ca": r.u8(),
        "u8_f0": r.u8(),
        "u8_f1": r.u8(),
        "u8_f2": r.u8(),
        "u8_f4": r.u8(),
    }
    if ver > 0x11:
        fields["u8_f3"] = r.u8()
    if ver > 0x16:
        fields["u32_50"] = r.u32()
        fields["u8_5e"] = r.u8()
    if ver > 0x13:
        names.append(r.lp_str())
    return {"names": names, "fields": fields}


def _read_driver_info(r: _R, ver: int) -> DriverInfo:
    """packer — DriverInfo."""
    off = r.pos
    strings = [r.lp_str() for _ in range(5)]
    if ver == 3:
        r.lp_str()  # discarded
    fields: Dict[str, Any] = {
        "u8_c8": r.u8(),
        "u16_ca": r.u16(),
        "u8_cc": r.u8(),
        "u32_d0": r.u32(),
        "u32_d4": r.u32(),
        "u32_d8": r.u32(),
        "u8_dc": r.u8(),
        "f32_e0": r.f32(),
        "f32_e4": r.f32(),
        "f32_e8": r.f32(),
        "f32_ec": r.f32(),
        "f32_f0": r.f32(),
        "f32_f4": r.f32(),
    }
    strings.append(r.lp_str())
    return DriverInfo(strings=strings, fields=fields, offset=off)


def _read_fe7a20(r: _R, ver: int) -> Tuple[List[int], List[ArrayBlob]]:
    """Setup / physics constants block."""
    vals: List[int] = []
    vals.append(r.u32())  # +0x28
    vals.append(r.u32())  # +0x30
    if ver > 5:
        vals.extend(r.u32() for _ in range(6))
    vals.extend(r.u32() for _ in range(4))  # +0x4c..+0x58
    if ver > 6:
        vals.append(r.u32())  # +0x2c
    arrays = [
        r.counted(4, "setup_payload_a_f32"),
        r.counted(4, "setup_payload_b_f32"),
    ]
    return vals, arrays


def _read_fdcc00(r: _R, ver: int, flag: int, name_prefix: str) -> List[ArrayBlob]:
    """packer snapshot / replayData payload (modern path)."""
    arrays: List[ArrayBlob] = []
    # always 0x10 header
    hdr_off = r.pos
    hdr = r.take(0x10)
    arrays.append(ArrayBlob(f"{name_prefix}.hdr16", 1, 0x10, hdr_off, hdr))

    if ver < 0x10:
        arrays.append(r.counted(0xB0, f"{name_prefix}.rec_b0"))
    elif ver > 0x17:
        arrays.append(r.counted(0x68, f"{name_prefix}.rec_68"))
    else:
        arrays.append(r.counted(0x80, f"{name_prefix}.rec_80"))  # <<7

    # shared tail from LAB_140fdd05b
    arrays.append(r.counted(0x30, f"{name_prefix}.arr_30"))
    if flag == 0:
        arrays.append(r.counted(0x48, f"{name_prefix}.arr_48"))
    arrays.append(r.counted(0x18, f"{name_prefix}.arr_18"))
    arrays.append(r.counted(0x10, f"{name_prefix}.arr_10"))
    arrays.append(r.opaque_bytes(f"{name_prefix}.raw_178"))
    arrays.append(r.opaque_bytes(f"{name_prefix}.raw_120"))
    arrays.append(r.opaque_bytes(f"{name_prefix}.raw_150"))

    # Version-gated tail: many more counted arrays. Walk conservatively for v>=24
    # For ver==0xd there is a special path; Spa is 25.
    if ver != 0xD:
        # Pattern repeats: u32 n + n*stride for several strides, plus more opaques.
        # Exact set depends on version and flag; read until we would break car framing
        # for ver > 0xf branches that don't early-return.
        extras = _fdcc00_modern_tail(r, ver, flag, name_prefix)
        arrays.extend(extras)
    return arrays


def _fdcc00_modern_tail(r: _R, ver: int, flag: int, name_prefix: str) -> List[ArrayBlob]:
    """Consume remaining packer arrays for ver>=16."""
    arrays: List[ArrayBlob] = []
    # version checks. We probe by reading in the order of the successful Spa
    # walk: if a count would overrun the file absurdly, raise.
    #
    # Practical approach used in testing: for v>=24 the next blocks are:
    # several more opaque/counted arrays. To avoid desync, we use the exact

    def maybe_counted(stride: int, label: str) -> None:
        arrays.append(r.counted(stride, f"{name_prefix}.{label}"))

    def maybe_opaque(label: str) -> None:
        arrays.append(r.opaque_bytes(f"{name_prefix}.{label}"))

    # Lines ~648–1020 condensed: many `u32 + n*k` and `u32 + n` pairs.
    # Empirically for Spa, after the first three opaques the function still has

    # Several reads use <<3, <<4, *0x10, *0x20, *0x28, opaque, etc.
    # Rather than risk desync on untested versions, for the car-level
    # `replayData` / snapshots we rely on `_read_car_record` which calls this
    # once per snapshot; if Spa parse fails mid-tail we fall back to opaque
    # remainder capture at car level.

    # Minimal safe modern tail observed necessary before next section magic
    # in car record (next fe6d80("replayData")):
    # version branches. For ver>0x17 path that did rec_68, it goto LAB_140fdd05b
    # then the shared tail we already consumed — then MORE versioned content.

    if ver >= 0x10:
        maybe_counted(4, "tail_u32s")  # speculative — may need adjustment
        # Actually DON'T guess — leave tail to car-record recovery.
        # Roll back this speculative read:
        arrays.pop()
        r.pos -= 4

    return arrays


def _read_car_records(r: _R, ver: int) -> Tuple[List[CarRecord], int, str]:
    """Detect SP (no count) vs MP (u16 count) car payload."""
    mark = r.pos
    sp_err: Exception = RuntimeError("single-car parse not attempted")
    # Try SP: immediate car record
    try:
        car = _read_car_record(r, ver, 0)
        # If nearly all bytes consumed or another car doesn't fit, SP wins
        if r.remaining() < 0x4A:
            return [car], 1, f"car_mode=single (SP) start=0x{mark:x}"
        # More data: attempt additional cars without counts
        cars = [car]
        idx = 1
        while r.remaining() >= 0x4A:
            cmark = r.pos
            try:
                cars.append(_read_car_record(r, ver, idx))
                idx += 1
            except Exception:
                r.pos = cmark
                break
        return cars, len(cars), f"car_mode=single+extra n={len(cars)} start=0x{mark:x}"
    except Exception as exc:
        sp_err = exc
        r.pos = mark

    # Try MP: u16 count then N records
    if r.remaining() < 2:
        return [], 0, f"car_mode=empty remaining={r.remaining()}"
    count = r.u16()
    if count == 0 or count > 128:
        r.pos = mark
        return [], 0, f"car_mode=fail SP={sp_err!r} bad_count={count}"
    cars = []
    try:
        for i in range(count):
            cars.append(_read_car_record(r, ver, i))
        return cars, count, f"car_mode=counted n={count} start=0x{mark:x}"
    except Exception as exc:
        r.pos = mark
        return [], 0, f"car_mode=fail SP={sp_err!r} MP={exc!r}"


def _read_car_record(r: _R, ver: int, index: int) -> CarRecord:
    """packer."""
    off = r.pos
    header_18 = r.take(0x18)
    header_30 = r.take(0x30)
    n_sub = r.u16()
    if ver <= 0x0F:
        sub_sz = 0x180
    elif ver <= 0x17:
        sub_sz = 0x150
    else:
        sub_sz = 0x138
    sub_blobs = [r.take(sub_sz) for _ in range(n_sub)]

    arrays: List[ArrayBlob] = []
    snapshots: List[SnapshotEntry] = []

    r.section_magic("snapshots")
    n_snap = r.u16()
    for si in range(n_snap):
        so = r.pos
        snap_id = r.u16()
        flag = r.u8()
        # packer — for modern ver use primary arrays; then replayData magic
        # Simplified: read fdcc00 core arrays then expect replayData magic per snap.
        # Full fdcc00 tail is version-complex; capture core then sync on 0x7B.
        snap_arrays = _read_fdcc00_core(r, ver, flag, f"car{index}.snap{si}")
        # Sync: find next section magic for replayData
        r.section_magic("replayData")
        # After magic, car record updates map key — no extra payload beyond magic
        # in the success path before next snap / bulk.
        snapshots.append(
            SnapshotEntry(snap_id=snap_id, flag=flag, arrays=snap_arrays, offset=so)
        )

    bulk = r.counted(0x10, f"car{index}.bulk_16")
    return CarRecord(
        index=index,
        header_18=header_18,
        header_30=header_30,
        sub_blobs=sub_blobs,
        snapshots=snapshots,
        bulk_16=bulk if bulk.count else None,
        arrays=arrays,
        offset=off,
        end_offset=r.pos,
    )


def _read_fdcc00_core(r: _R, ver: int, flag: int, prefix: str) -> List[ArrayBlob]:
    """Core of packer through shared tail (before version-only extras)."""
    arrays: List[ArrayBlob] = []
    hdr_off = r.pos
    arrays.append(ArrayBlob(f"{prefix}.hdr16", 1, 0x10, hdr_off, r.take(0x10)))

    if ver < 0x10:
        arrays.append(r.counted(0xB0, f"{prefix}.rec_b0"))
    elif ver > 0x17:
        arrays.append(r.counted(0x68, f"{prefix}.rec_68"))
    else:
        arrays.append(r.counted(0x80, f"{prefix}.rec_80"))

    arrays.append(r.counted(0x30, f"{prefix}.arr_30"))
    if flag == 0:
        arrays.append(r.counted(0x48, f"{prefix}.arr_48"))
    arrays.append(r.counted(0x18, f"{prefix}.arr_18"))
    arrays.append(r.counted(0x10, f"{prefix}.arr_10"))
    arrays.append(r.opaque_bytes(f"{prefix}.raw_a"))
    arrays.append(r.opaque_bytes(f"{prefix}.raw_b"))
    arrays.append(r.opaque_bytes(f"{prefix}.raw_c"))

    # Consume remaining fdcc00 body until the next 0x7B section magic without
    # eating the magic itself. Snapshot loop expects replayData magic next.
    start = r.pos
    # Heuristic sync: scan forward for 0x7B u32 that is followed by plausible data;
    drained = _drain_fdcc00_until_magic(r, ver, flag, prefix)
    arrays.extend(drained)
    if r.pos == start:
        pass
    return arrays


def _drain_fdcc00_until_magic(
    r: _R, ver: int, flag: int, prefix: str
) -> List[ArrayBlob]:
    """Parse remaining fdcc00 fields for ver>=24 until next 0x7B is at cursor."""
    arrays: List[ArrayBlob] = []
    # long chain. We implement the reads that are NOT behind `if (ver == 0xd)`

    # Helper: peek if next u32 is section magic — stop (leave it for caller).
    def at_magic() -> bool:
        if r.remaining() < 4:
            return False
        return struct.unpack_from("<I", r.data, r.pos)[0] == SECTION_MAGIC

    if at_magic():
        return arrays

    # fall back to scanning for 0x7B.
    try:
        seq: List[Tuple[str, int]] = [
            # label, stride (1 = opaque byte blob)
        ]
        # first big array was 0x68 and jumped to shared tail — shared tail done.
        # Then more content from ~648. Extract explicit strides:
        # Looking at lines 648-1020 for common patterns with *N or <<k:
        # We'll iteratively: if at_magic break; else read u32 n; if n==0 continue;
        # if n*candidate_stride fits and next would still leave magic later — hard.
        #
        # Safer Spa-specific approach: scan for the next aligned 0x7B that begins
        # a section, treating intervening bytes as one opaque remainder.
        if not at_magic():
            scan = r.pos
            limit = min(len(r.data) - 4, r.pos + 50_000_000)
            found = None
            while scan <= limit:
                if struct.unpack_from("<I", r.data, scan)[0] == SECTION_MAGIC:
                    # replayData magic should be followed by either end of snaps
                    # loop machinery or another snap's u16 — accept first 0x7B.
                    found = scan
                    break
                scan += 1
            if found is None:
                raise RpyError(f"{prefix}: no section magic after fdcc00 core at 0x{r.pos:x}")
            opaque = r.take(found - r.pos)
            if opaque:
                arrays.append(
                    ArrayBlob(
                        f"{prefix}.fdcc00_tail_opaque",
                        len(opaque),
                        1,
                        found - len(opaque),
                        opaque,
                    )
                )
    except RpyTruncated:
        raise
    return arrays


def car_snapshots(ex: "RpyExtract") -> List[SnapshotEntry]:
    """One snapshot per car, in file order. Race replays store every car as a snapshot of one record."""
    return [sn for rec in (ex.car_records or []) for sn in rec.snapshots]


def player_car_index(ex: "RpyExtract") -> int:
    """Index (into ``car_snapshots``) of the recording driver's car: the one with per-wheel data."""
    snaps = car_snapshots(ex)
    for i, sn in enumerate(snaps):
        if any(a.stride == 0x48 and a.count > 0 for a in sn.arrays):
            return i
    return 0


def snapshot_arrays(ex: "RpyExtract", car_index: Optional[int] = None) -> Optional[List[ArrayBlob]]:
    snaps = car_snapshots(ex)
    if not snaps:
        return None
    i = player_car_index(ex) if car_index is None else int(car_index)
    if i < 0 or i >= len(snaps):
        return None
    return snaps[i].arrays


def list_cars(ex: "RpyExtract") -> List[Dict[str, Any]]:
    """Cars in the replay with driver names (from carInfo) and what data each has."""
    names = {c.car_key: [d.strings[0] for d in c.drivers if d.strings] for c in (ex.cars_info or [])}
    player = player_car_index(ex)
    out = []
    for i, sn in enumerate(car_snapshots(ex)):
        cnt = {a.stride: a.count for a in sn.arrays if a.stride in (0x68, 0x30, 0x48, 0x18)}
        out.append({
            "car_index": i,
            "car_key": sn.snap_id,
            "drivers": names.get(sn.snap_id, []),
            "is_player": i == player,
            "pose_frames": cnt.get(0x68, 0),
            "input_records": cnt.get(0x30, 0),
            "has_wheel_data": cnt.get(0x48, 0) > 0,
        })
    return out


def extract_rpy(path: str | Path) -> RpyExtract:
    path = Path(path)
    data = path.read_bytes()
    r = _R(data)
    notes: List[str] = []

    version = r.u16()
    if not (3 <= version <= 25):
        notes.append(f"version {version} outside known supported 3..25")

    # open_check: versions (ver-3) < 0x17 → 3..25
    session_blob = r.take(0x30)
    track_id_check = struct.unpack_from("<i", session_blob, 0)[0]

    session_name = ""
    if version > 0x14:
        session_name = r.lp_str()

    player_car_model = None
    if version > 3:
        player_car_model = r.u32()

    if version > 0x0B:
        r.u8()  # +0x68
    # +0x69 defaults 0xFF; read if ver > 0x10
    if version > 0x10:
        r.u8()
    else:
        pass

    setup_u32s, setup_arrays = _read_fe7a20(r, version)
    opaque: List[ArrayBlob] = list(setup_arrays)

    r.section_magic("weatherData")
    weather_data = [r.f32() for _ in range(7)]

    r.section_magic("weatherStatus")
    n_ws = r.u16()
    weather_status = []
    for _ in range(n_ws):
        wid = r.u16()
        ent = _read_weather_status_entry(r, version)
        ent["id"] = wid
        weather_status.append(ent)

    r.section_magic("carInfo")
    n_cars = r.u16()
    cars_info: List[CarInfoEntry] = []
    for _ in range(n_cars):
        car_key = r.u16()
        n_drv = r.u16()
        drivers = [_read_driver_info(r, version) for _ in range(n_drv)]
        cars_info.append(CarInfoEntry(car_key=car_key, drivers=drivers))

    r.section_magic("driverInfo")
    di30: List[bytes] = []
    di20: List[bytes] = []
    if version > 0x15:
        n30 = r.u32()
        for _ in range(n30):
            di30.append(r.take(0x30))
        n20 = r.u32()
        for _ in range(n20):
            di20.append(r.take(0x20))

    # Trailing session opaques / leaderboard (Spa v25 path)
    opaque.append(r.opaque_bytes("sess_blob_58"))
    if version >= 0x0B:
        opaque.append(r.opaque_bytes("sess_blob_62"))
    opaque.append(r.opaque_bytes("sess_blob_74"))
    if version >= 0x19:
        opaque.append(r.opaque_bytes("sess_blob_7e"))
    else:
        # legacy 0x24 records — treat as opaque byte blob via u32 length path used in old code
        opaque.append(r.opaque_bytes("sess_blob_7e_legacy"))
    opaque.append(r.opaque_bytes("sess_blob_98"))
    opaque.append(r.opaque_bytes("sess_blob_b2"))
    if version > 9:
        # Single-player saves have one more blob (per-lap records) before the leaderboard magic;
        # multiplayer saves can reach the magic directly. A blob length of 123 is
        # indistinguishable from the magic (0x7B), so prefer the magic when it is at the cursor.
        if struct.unpack_from("<I", r.data, r.pos)[0] != SECTION_MAGIC:
            opaque.append(r.opaque_bytes("sess_blob_3a"))
        r.section_magic("leaderboard")
    if version > 7:
        opaque.append(r.opaque_bytes("sess_blob_bc"))

    # After header: SP Saved/Highlights write one packer with no count
    # prefix. Multi-car (flag+0xC==1) writes u16 count then N records.
    car_slot_count = 0
    car_records: List[CarRecord] = []
    if r.remaining() >= 0x4A:
        mark = r.pos
        car_records, car_slot_count, mode_note = _read_car_records(r, version)
        notes.append(mode_note)
        if not car_records and r.remaining():
            r.pos = mark
            rem = r.take(r.remaining())
            opaque.append(
                ArrayBlob("car_parse_remainder", len(rem), 1, mark, rem)
            )

    return RpyExtract(
        path=str(path),
        size=len(data),
        version=version,
        track_id_check=track_id_check,
        session_name=session_name,
        player_car_model=player_car_model,
        setup_u32s=setup_u32s,
        setup_f32s=_f32s_from_u32s(setup_u32s),
        weather_data=weather_data,
        weather_status=weather_status,
        cars_info=cars_info,
        driver_info_30=di30,
        driver_info_20=di20,
        opaque_blobs=opaque,
        car_slot_count=car_slot_count,
        car_records=car_records,
        parse_notes=notes,
        remaining_bytes=r.remaining(),
        end_offset=r.pos,
    )


def extract_to_dict(ex: RpyExtract, include_raw: bool = False) -> Dict[str, Any]:
    def driver_d(d: DriverInfo) -> Dict[str, Any]:
        return {"offset": d.offset, "strings": d.strings, "fields": d.fields}

    cars = []
    for c in ex.cars_info:
        cars.append(
            {
                "car_key": c.car_key,
                "drivers": [driver_d(d) for d in c.drivers],
            }
        )

    car_recs = []
    for cr in ex.car_records:
        snaps = []
        for s in cr.snapshots:
            snaps.append(
                {
                    "id": s.snap_id,
                    "flag": s.flag,
                    "offset": s.offset,
                    "arrays": [a.summary() for a in s.arrays],
                }
            )
        rec: Dict[str, Any] = {
            "index": cr.index,
            "offset": cr.offset,
            "end_offset": cr.end_offset,
            "header_18_hex": cr.header_18.hex(),
            "header_30_hex": cr.header_30.hex(),
            "sub_blob_count": len(cr.sub_blobs),
            "sub_blob_bytes": len(cr.sub_blobs[0]) if cr.sub_blobs else 0,
            "snapshots": snaps,
            "bulk_16": cr.bulk_16.summary() if cr.bulk_16 else None,
        }
        if include_raw and cr.bulk_16:
            rec["bulk_16_b64_nbytes"] = cr.bulk_16.nbytes
        car_recs.append(rec)

    from vre_rpy.parse_rpy import extract_lap_times_ms

    lap_info = extract_lap_times_ms(Path(ex.path).read_bytes()) if ex.path else {}

    try:
        from vre_rpy.env_decode import env_summary_from_extract
    except ImportError:
        from env_decode import env_summary_from_extract  # type: ignore

    base = {
        "path": ex.path,
        "size": ex.size,
        "version": ex.version,
        "track_id_check": ex.track_id_check,
        "session_name": ex.session_name,
        "player_car_model": ex.player_car_model,
        "setup_f32s": ex.setup_f32s,
        "weather_data": ex.weather_data,
        "weather_status": ex.weather_status,
        "cars_info": cars,
        "driver_info_30_count": len(ex.driver_info_30),
        "driver_info_20_count": len(ex.driver_info_20),
        "opaque_blobs": [a.summary() for a in ex.opaque_blobs],
        "car_slot_count": ex.car_slot_count,
        "car_records": car_recs,
        "lap_times_ms": lap_info.get("lap_times_ms", []),
        "flying_lap_times_ms": lap_info.get("flying_lap_times_ms", []),
        "best_flying_lap_ms": lap_info.get("best_flying_lap_ms"),
        "lap_time_stream": lap_info.get("stream"),
        "parse_notes": ex.parse_notes,
        "end_offset": ex.end_offset,
        "remaining_bytes": ex.remaining_bytes,
        "consumed_frac": round(ex.end_offset / ex.size, 6) if ex.size else 0,
    }
    base["env"] = env_summary_from_extract(base)
    from .lap_validity import RPY_VALIDITY_GO as _VGO

    base["RPY_VALIDITY_GO"] = _VGO
    from .parse_rpy import RPY_TELEMETRY_GO as _GO

    base["RPY_TELEMETRY_GO"] = _GO
    return base


def iter_pose_samples(ex: RpyExtract, car_index: Optional[int] = None) -> List[Dict[str, float]]:
    """Decode primary 0x68-stride pose stream (world x/y/z + secondary copy).

    Layout hypothesis (Spa/Monza/Imola v25, not yet MoTeC-locked for GO):
      f32[0], f32[1], f32[2] = position
      f32[5], f32[6], f32[7] = nearby/secondary position
      f32[4] ≈ stable scalar (~heading-related constant on Spa)
      f32[15] (off 60) ≈ elevation-like span on Spa
    """
    arrays = snapshot_arrays(ex, car_index)
    if not arrays:
        return []
    pose = next((a for a in arrays if a.stride == 0x68), None)
    if pose is None or pose.count == 0:
        return []
    out: List[Dict[str, float]] = []
    for i in range(pose.count):
        row = struct.unpack_from("<26f", pose.data, i * 0x68)
        out.append(
            {
                "i": float(i),
                "x": row[0],
                "y": row[1],
                "z": row[2],
                "x2": row[5],
                "y2": row[6],
                "z2": row[7],
                "f4": row[4],
                "elev": row[15],
            }
        )
    return out


def write_pose_csv(ex: RpyExtract, path: Path, car_index: Optional[int] = None, stride: int = 1) -> int:
    import csv

    samples = iter_pose_samples(ex, car_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["i", "x", "y", "z", "x2", "y2", "z2", "f4", "elev"])
        w.writeheader()
        for i, s in enumerate(samples):
            if i % stride == 0:
                w.writerow(s)
    return len(samples)


def export_arrays(ex: RpyExtract, out_dir: Path) -> List[str]:
    """Write every ArrayBlob to out_dir as raw .bin (+ .json sidecar summary)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    blobs: List[ArrayBlob] = list(ex.opaque_blobs)
    for cr in ex.car_records:
        if cr.bulk_16:
            blobs.append(cr.bulk_16)
        for s in cr.snapshots:
            blobs.extend(s.arrays)
        for i, sub in enumerate(cr.sub_blobs):
            blobs.append(
                ArrayBlob(f"car{cr.index}.sub{i}", 1, len(sub), cr.offset, sub)
            )

    for i, b in enumerate(blobs):
        if not b.data:
            continue
        safe = b.name.replace("/", "_").replace(".", "_")
        bin_path = out_dir / f"{i:03d}_{safe}.bin"
        bin_path.write_bytes(b.data)
        (out_dir / f"{i:03d}_{safe}.json").write_text(
            json.dumps(b.summary(), indent=2) + "\n"
        )
        written.append(str(bin_path))
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("rpy", type=Path)
    ap.add_argument("--json", action="store_true", help="print JSON summary")
    ap.add_argument("--export-dir", type=Path, help="dump array blobs here")
    ap.add_argument("--pose-csv", type=Path, help="write decoded pose x/y/z CSV")
    ap.add_argument("--pose-stride", type=int, default=1, help="keep every Nth pose row")
    ap.add_argument("--include-raw-meta", action="store_true")
    args = ap.parse_args()

    ex = extract_rpy(args.rpy)
    d = extract_to_dict(ex, include_raw=args.include_raw_meta)
    if args.export_dir:
        written = export_arrays(ex, args.export_dir)
        d["exported"] = written
        (args.export_dir / "extract_summary.json").write_text(
            json.dumps(d, indent=2) + "\n"
        )
        if not args.pose_csv:
            args.pose_csv = args.export_dir / "pose_xyz.csv"
    if args.pose_csv:
        n = write_pose_csv(ex, args.pose_csv, stride=max(1, args.pose_stride))
        d["pose_csv"] = str(args.pose_csv)
        d["pose_samples"] = n
    if args.json or not (args.export_dir or args.pose_csv):
        print(json.dumps(d, indent=2))
    else:
        print(
            f"version={ex.version} cars={ex.car_slot_count} "
            f"consumed={ex.end_offset}/{ex.size} remaining={ex.remaining_bytes}"
        )
        for n in ex.parse_notes:
            print("note:", n)
        if args.pose_csv:
            print("pose_csv:", args.pose_csv)
    return 0 if ex.remaining_bytes == 0 and not any(
        "failed" in n for n in ex.parse_notes
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
