"""Patch Motec .mt2 Default section names with real corner labels.

ACC track maps ship with auto names like ``Turn 1`` / ``Str 0-1``. We rename
corner sections using ``track_pack/*_acc.json`` display names,
matching by distance along the Motec track map (marker Dist midpoints ↔
corner apex_spline × length_m).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_ascii_key(data: bytes, i: int) -> Optional[Tuple[str, int]]:
    if i + 4 > len(data):
        return None
    n = struct.unpack_from("<I", data, i)[0]
    if not (1 <= n <= 64) or i + 4 + n > len(data):
        return None
    raw = data[i + 4 : i + 4 + n]
    if not all(32 <= b < 127 for b in raw):
        return None
    return raw.decode("ascii"), i + 4 + n


def iter_named_utf16_fields(data: bytes) -> List[Tuple[int, int, str]]:
    """Return list of (name_value_offset, byte_len, text) for Name→UTF16 pairs."""
    out: List[Tuple[int, int, str]] = []
    i = 0
    while i < len(data) - 8:
        key = _read_ascii_key(data, i)
        if key and key[0] == "Name":
            j = key[1]
            if j + 4 <= len(data):
                blen = struct.unpack_from("<I", data, j)[0]
                if 2 <= blen <= 128 and blen % 2 == 0 and j + 4 + blen <= len(data):
                    raw = data[j + 4 : j + 4 + blen]
                    try:
                        text = raw.decode("utf-16-le")
                    except Exception:
                        i += 1
                        continue
                    if text.isprintable() and text.strip():
                        out.append((j, blen, text))
                        i = j + 4 + blen
                        continue
        i += 1
    return out


def parse_markers_and_turns(
    data: bytes,
) -> Tuple[Dict[str, float], List[Tuple[str, float, int, int]]]:
    """Return (marker_name→dist_m, list of (turn_name, mid_dist, name_off, blen))."""
    markers: Dict[str, float] = {}
    i = 0
    while i < len(data) - 20:
        key = _read_ascii_key(data, i)
        if not key or key[0] != "Dist":
            i += 1
            continue
        j = key[1]
        blen = struct.unpack_from("<I", data, j)[0]
        if blen not in (4, 8) or j + 4 + blen > len(data):
            i += 1
            continue
        raw = data[j + 4 : j + 4 + blen]
        dist = struct.unpack("<d" if blen == 8 else "<f", raw)[0]
        k = j + 4 + blen
        nk = _read_ascii_key(data, k)
        if nk and nk[0] == "Name":
            nlen = struct.unpack_from("<I", data, nk[1])[0]
            if 2 <= nlen <= 64 and nlen % 2 == 0:
                name = data[nk[1] + 4 : nk[1] + 4 + nlen].decode("utf-16-le", errors="replace")
                if name.startswith("Marker"):
                    markers[name] = float(dist)
                i = nk[1] + 4 + nlen
                continue
        i += 1

    # Turn sections: Name "Turn N" preceded by Marker1/Marker2 refs in nearby bytes
    fields = iter_named_utf16_fields(data)
    turns: List[Tuple[str, float, int, int]] = []
    for offset, blen, text in fields:
        if not text.startswith("Turn "):
            continue
        # look backward ~120 bytes for Marker1 / Marker2 utf16 names
        window = data[max(0, offset - 200) : offset]
        m1 = m2 = None
        for mn in markers:
            enc = mn.encode("utf-16-le")
            if enc in window:
                # last two marker mentions → Marker1 then Marker2 order in blob
                if m1 is None:
                    m1 = mn
                else:
                    m2 = mn
        if m1 and m2 and m1 in markers and m2 in markers:
            mid = 0.5 * (markers[m1] + markers[m2])
        elif m1 and m1 in markers:
            mid = markers[m1]
        else:
            mid = float("nan")
        turns.append((text, mid, offset, blen))
    return markers, turns


def corner_rename_by_distance(
    track_config: Dict[str, Any],
    turns: List[Tuple[str, float, int, int]],
) -> Dict[str, str]:
    """Greedy match Motec Turn sections → corner display names by apex distance."""
    length = float(track_config.get("length_m") or 0.0)
    corners = track_config.get("corners") or []
    if length <= 1.0 or not corners or not turns:
        # fallback: sequential Turn N → display_name order
        labels = [
            str(c.get("display_name") or c.get("name") or f"Corner {i + 1}")
            for i, c in enumerate(corners)
        ]
        renames: Dict[str, str] = {}
        ordered = [t[0] for t in turns]
        seen = set()
        uniq: List[str] = []
        for n in ordered:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        for i, name in enumerate(uniq):
            if i < len(labels):
                renames[name] = labels[i]
        return renames

    targets: List[Tuple[str, float]] = []
    for c in corners:
        apex = c.get("apex_spline")
        if apex is None:
            a0 = float(c.get("spline_start") or 0.0)
            a1 = float(c.get("spline_end") or a0)
            apex = 0.5 * (a0 + a1)
        label = str(c.get("display_name") or c.get("name") or "Corner")
        targets.append((label, float(apex) * length))

    # unique turns with finite mid
    cand: List[Tuple[str, float]] = []
    seen = set()
    for name, mid, _, _ in turns:
        if name in seen or mid != mid:
            continue
        seen.add(name)
        cand.append((name, mid))

    renames = {}
    used_turns: set[str] = set()
    for label, apex_m in targets:
        best = None
        best_d = 1e18
        for name, mid in cand:
            if name in used_turns:
                continue
            d = abs(mid - apex_m)
            if d < best_d:
                best_d = d
                best = name
        # allow up to ~400m mismatch (Motec section granularity)
        if best is not None and best_d < max(450.0, length * 0.08):
            renames[best] = label
            used_turns.add(best)
    return renames


def patch_mt2_section_names(
    mt2_path: Path | str,
    renames: Dict[str, str],
    *,
    out_path: Optional[Path | str] = None,
) -> Path:
    """Rewrite UTF-16 section ``Name`` values; adjusts length fields + shifts tail."""
    path = Path(mt2_path)
    data = bytearray(path.read_bytes())
    fields = iter_named_utf16_fields(bytes(data))
    for offset, blen, text in reversed(fields):
        new = renames.get(text)
        if not new or new == text:
            continue
        new_bytes = new.encode("utf-16-le")
        old_total = 4 + blen
        replacement = struct.pack("<I", len(new_bytes)) + new_bytes
        data[offset : offset + old_total] = replacement
    out = Path(out_path) if out_path else path
    out.write_bytes(bytes(data))
    return out


def patch_mt2_from_track_config(
    mt2_path: Path | str,
    track_config: Dict[str, Any],
    *,
    out_path: Optional[Path | str] = None,
) -> Tuple[Path, Dict[str, str]]:
    data = Path(mt2_path).read_bytes()
    _markers, turns = parse_markers_and_turns(data)
    renames = corner_rename_by_distance(track_config, turns)
    out = patch_mt2_section_names(mt2_path, renames, out_path=out_path)
    return out, renames


_MT2_TO_TRACK_ID = {
    "spa.mt2": "spa_acc",
    "monza.mt2": "monza_acc",
    "barcelona.mt2": "barcelona_acc",
    "brands_hatch.mt2": "brands_hatch_acc",
    "hungaroring.mt2": "hungaroring_acc",
    "imola.mt2": "imola_acc",
    "silverstone.mt2": "silverstone_acc",
    "paul_ricard.mt2": "paul_ricard_acc",
    "nurburgring.mt2": "nurburgring_acc",
    "nurburgring -- grand prix.mt2": "nurburgring_acc",
    "zandvoort.mt2": "zandvoort_acc",
    "zolder.mt2": "zolder_acc",
    "misano.mt2": "misano_acc",
    "donington.mt2": "donington_acc",
    "oulton_park.mt2": "oulton_park_acc",
    "snetterton.mt2": "snetterton_acc",
    "mount_panorama.mt2": "mount_panorama_acc",
    "laguna_seca.mt2": "laguna_seca_acc",
    "suzuka.mt2": "suzuka_acc",
    "kyalami.mt2": "kyalami_acc",
    "valencia.mt2": "valencia_acc",
}


def track_id_for_mt2(mt2_name: str) -> Optional[str]:
    return _MT2_TO_TRACK_ID.get(mt2_name.strip().lower())


def load_track_config(track_id: str, repo_root=None):
    """Load corner/sector config from the bundled track_pack."""
    import json
    from pathlib import Path
    pack_dir = Path(__file__).resolve().parent / "track_pack"
    key = track_id.strip().lower().replace("-", "_")
    candidates = [
        pack_dir / f"{key}.json",
        pack_dir / f"{key}_acc.json",
    ]
    if "spa" in key:
        candidates.insert(0, pack_dir / "spa_acc.json")
    if "monza" in key:
        candidates.insert(0, pack_dir / "monza_acc.json")
    for cand in candidates:
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    return {"track_id": key, "corners": [], "sectors": [], "length_m": 0}


