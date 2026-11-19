"""Build Motec .ld workbooks from decoded .rpy arr_30/arr_48 streams."""

from __future__ import annotations

import datetime
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .arr30_decode import flat_channel_map


def _ensure_ldparser() -> None:
    try:
        import ldparser  # noqa: F401

        return
    except ImportError:
        pass
    for cand in (
        Path.home() / "tools" / "ldparser",
        Path("/tmp/ldparser"),
        Path("/home/pranav/tools/ldparser"),
    ):
        if (cand / "ldparser.py").is_file():
            p = str(cand)
            if p not in sys.path:
                sys.path.insert(0, p)
            return


def _hz_from_time(time_s: Optional[np.ndarray]) -> float:
    if time_s is None or len(time_s) < 2:
        return 50.0
    dt = float(np.median(np.diff(time_s.astype(np.float64))))
    if dt <= 1e-6:
        return 50.0
    return float(max(1.0, min(200.0, round(1.0 / dt))))


_UNITS = {
    "TIME": "s",
    "THROTTLE": "%",
    "BRAKE": "%",
    "STEERANGLE": "deg",
    "GEAR": "gear",
    "SPEED": "m/s",
    "SPEED_KMH": "km/h",
    "RPMS": "rpm",
    "G_LAT": "g",
    "G_LON": "g",
    "VRE_COAST": "flag",
    "VRE_PEDAL_GAP": "s",
    "VRE_STEER_RATE": "deg/s",
    "VRE_SPEED_KMH": "km/h",
    "VRE_US_OS": "deg",
    "VRE_YAW_DEG": "deg/s",
    "VRE_SPIN": "flag",
    "VRE_LOCK": "flag",
    "VRE_TC_ACT": "flag",
    "VRE_ABS_ACT": "flag",
    "VRE_TC_LEVEL": "map",
    "VRE_ABS_LEVEL": "map",
    "VRE_TCCUT": "map",
    "VRE_ENG_MAP": "map",
    "VRE_RAKE_LOAD": "N",
    "VRE_LOAD_F": "N",
    "VRE_LOAD_R": "N",
    "VRE_SLIP_MAX": "ratio",
    "VRE_SLIP_F": "ratio",
    "VRE_SLIP_R": "ratio",
    "VRE_BT_BAL": "C",
    "VRE_TRACK_GRIP": "0-1",
    "VRE_LAT_UTIL": "util",
    "VRE_G_LAT": "g",
    "VRE_G_LON": "g",
}


def _unit_for(name: str) -> str:
    if name in _UNITS:
        return _UNITS[name]
    if name.startswith("BRAKE_TEMP_"):
        return "C"
    if name.startswith("WHEEL_SPEED_"):
        return "m/s"
    if name.startswith("WHEEL_ANGULAR_"):
        return "rad/s"
    if name.startswith("TYRE_LOAD_"):
        return "N"
    if name.startswith("SLIP_RATIO_") or name.startswith("ND_SLIP_"):
        return "ratio"
    if name.startswith("WHEEL_DIRT_") or name.startswith("MARBLES_"):
        return "0-1"
    if name.startswith("VRE_"):
        return "vre"
    return ""


def channels_from_decoded(
    decoded: Dict[str, Any],
    *,
    prefix: str = "",
    track_grip: Optional[float] = None,
    steering_lock_deg: float = 400.0,
    electronics: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    from .setup_calcs import compute_setup_channels

    flat = flat_channel_map(decoded)
    # Motec log time starts at 0 for sample 0. ACC session clocks (Erik ~1714s)
    # must be shifted or beacons/laps land past end-of-log.
    if flat.get("TIME") is not None:
        t_abs = np.asarray(flat["TIME"], dtype=np.float64)
        flat["TIME"] = (t_abs - float(t_abs[0])).astype(np.float64)
    hz = _hz_from_time(flat.get("TIME"))
    flat.update(
        compute_setup_channels(
            decoded,
            track_grip=track_grip,
            steering_lock_deg=steering_lock_deg,
            electronics=electronics,
        )
    )
    p = f"{prefix}_" if prefix else ""
    out: List[Dict[str, Any]] = []
    # Do NOT export TIME as a Motec channel. ACC .ld files have no TIME channel;
    # Motec times samples as index/freq. A TIME channel makes Motec mis-scale
    # .ldx BCN ticks (~10x lap times, e.g. 23:21 instead of 2:19).
    order = [
        "THROTTLE",
        "BRAKE",
        "STEERANGLE",
        "GEAR",
        "SPEED",
        "SPEED_KMH",
        "RPMS",
        "G_LAT",
        "G_LON",
    ]
    for c in ("LF", "RF", "LR", "RR"):
        order += [
            f"WHEEL_SPEED_{c}",
            f"WHEEL_ANGULAR_{c}",
            f"BRAKE_TEMP_{c}",
            f"ND_SLIP_{c}",
            f"TYRE_LOAD_{c}",
            f"SLIP_RATIO_{c}",
            f"MARBLES_{c}",
            f"WHEEL_DIRT_{c}",
        ]
    order += [
        "VRE_COAST",
        "VRE_PEDAL_GAP",
        "VRE_STEER_RATE",
        "VRE_SPEED_KMH",
        "VRE_US_OS",
        "VRE_YAW_DEG",
        "VRE_SPIN",
        "VRE_LOCK",
        "VRE_TC_ACT",
        "VRE_ABS_ACT",
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
        "VRE_COMBINED_G",
        "VRE_TRAIL_BRAKE",
        "VRE_BRAKE_RATE",
        "VRE_THROTTLE_RATE",
        "VRE_BRAKE_RELEASE",
        "VRE_DIST_M",
    ]

    seen = set()
    for name in order:
        if name not in flat or name in seen:
            continue
        seen.add(name)
        short = name.replace("WHEEL_SPEED_", "WS").replace("WHEEL_ANGULAR_", "WA")
        short = short.replace("BRAKE_TEMP_", "BT").replace("TYRE_LOAD_", "TL")
        short = short.replace("SLIP_RATIO_", "SR").replace("WHEEL_DIRT_", "WD")
        out.append(
            {
                "name": f"{p}{name}"[:32],
                "short_name": (f"{prefix[:2]}{short}" if prefix else short)[:8],
                "unit": _unit_for(name)[:12],
                "freq": hz,
                "data": np.asarray(flat[name], dtype=np.float32),
            }
        )
    return out


# ACC Motec .mt2 basenames (venue header must match for Track Map lookup).
_VENUE_BY_TRACK = {
    "spa": "Spa",
    "spa_acc": "Spa",
    "monza": "monza",
    "monza_acc": "monza",
    "brands_hatch": "brands_hatch",
    "brandshatch": "brands_hatch",
    "hungaroring": "Hungaroring",
    "imola": "Imola",
    "silverstone": "Silverstone",
    "paul_ricard": "Paul_Ricard",
    "paulricard": "Paul_Ricard",
    "nurburgring": "nurburgring",
    "barcelona": "Barcelona",
    "zandvoort": "Zandvoort",
    "zolder": "Zolder",
    "misano": "misano",
    "donington": "donington",
    "oulton_park": "oulton_park",
    "snetterton": "snetterton",
    "mount_panorama": "mount_panorama",
    "bathurst": "mount_panorama",
    "laguna_seca": "Laguna_Seca",
    "suzuka": "Suzuka",
    "kyalami": "Kyalami",
    "valencia": "Valencia",
    "kyalami_acc": "Kyalami",
}


def motec_venue_from_track(
    track_id: Optional[str] = None,
    *,
    filename_hints: Optional[Dict[str, Any]] = None,
) -> str:
    """Map rpy track id / filename hint → Motec venue string (.mt2 basename)."""
    candidates: List[str] = []
    if track_id:
        candidates.append(str(track_id).strip().lower())
    if filename_hints:
        for hk in ("track", "track_id"):
            hint = filename_hints.get(hk)
            if hint:
                candidates.append(str(hint).strip().lower())
        # whole filename stem sometimes embeds track (…_Spa_ERIK)
        name = filename_hints.get("name") or filename_hints.get("base")
        if name:
            candidates.append(str(name).strip().lower())

    for raw in candidates:
        key = raw.replace("-", "_").replace(" ", "_")
        if key in _VENUE_BY_TRACK:
            return _VENUE_BY_TRACK[key]
        key2 = key.replace("_acc", "")
        if key2 in _VENUE_BY_TRACK:
            return _VENUE_BY_TRACK[key2]
        for k, v in _VENUE_BY_TRACK.items():
            if k in key:
                return v
    return "ACC"


def format_motec_lap_time(seconds: float) -> str:
    """Motec Details Fastest Time style: ``2:19.072`` / ``0:51.064``."""
    s = max(0.0, float(seconds))
    mins = int(s // 60.0)
    rem = s - 60.0 * mins
    return f"{mins}:{rem:06.3f}"


def snap_beacons_to_sample_axis(
    time_s: np.ndarray,
    beacon_times_s: Sequence[float],
    *,
    freq: float,
) -> List[float]:
    """Map Motec-relative TIME seconds → sample index/freq (Motec's true axis)."""
    t = np.asarray(time_s, dtype=np.float64)
    if t.size < 2:
        return [float(x) for x in beacon_times_s]
    t_rel = t - float(t[0])
    freq = float(freq) if freq else _hz_from_time(t_rel)
    out: List[float] = []
    for b in beacon_times_s:
        i = int(np.argmin(np.abs(t_rel - float(b))))
        out.append(float(i) / freq)
    return out


def beacon_times_from_timer_laps(
    laps: Sequence[Dict[str, Any]],
    *,
    t_min: float,
    t_max: float,
    min_gap_s: float = 1.0,
    motec_t0: float = 0.0,
) -> List[float]:
    """S/F beacon times on Motec's log axis (seconds from first sample).

    arr24 ``session_*_s`` is ACC session clock (Erik may start at ~1700s). Motec
    i2 times the .ld from 0..(n/freq); beacons past the log end make Motec show
    garbage lap times (e.g. 21:50). Subtract ``motec_t0`` (= TIME[0]) so beacons
    sit on the same axis as the exported TIME channel.
    """
    t_min = float(t_min)
    t_max = float(t_max)
    t0 = float(motec_t0)
    if t_max <= t_min:
        return [0.0, max(0.0, t_max - t0)] if t_max > t_min else [0.0, 1.0]
    if not laps:
        return [0.0, max(0.0, t_max - t0)]

    times: List[float] = [float(laps[0]["session_start_s"])]
    for lap in laps:
        times.append(float(lap["session_end_s"]))

    cleaned: List[float] = []
    lo = max(0.0, t_min - t0)
    hi = max(lo, t_max - t0)
    for raw in times:
        t = max(lo, min(hi, float(raw) - t0))
        if not cleaned or (t - cleaned[-1]) >= min_gap_s:
            cleaned.append(t)
    if len(cleaned) < 2:
        return [lo, hi]
    return cleaned


def _flying_gap_stats(
    beacon_times_s: Sequence[float],
    *,
    min_lap_s: float = 60.0,
    max_lap_s: float = 600.0,
    preferred_fastest_s: Optional[float] = None,
) -> Tuple[int, float, int]:
    """Return (total_laps, fastest_s, fastest_1based_lap_index).

    When ``preferred_fastest_s`` is set (arr24 completed ``lap_ms``/1000), use it
    for Details Fastest Time — beacon gaps can be 1–3 ms off Motec gold.
    """
    gaps = [
        float(beacon_times_s[i + 1]) - float(beacon_times_s[i])
        for i in range(len(beacon_times_s) - 1)
    ]
    total = len(gaps)
    flying = [(i + 1, g) for i, g in enumerate(gaps) if min_lap_s <= g <= max_lap_s]
    # Drop a short final in-lap (incomplete session tail) so it cannot beat real flyers.
    if len(flying) >= 2 and flying[-1][0] == total:
        prior = [g for _, g in flying[:-1]]
        if prior and flying[-1][1] < 0.98 * min(prior):
            flying = flying[:-1]
    if not flying:
        if not gaps:
            return 0, float(preferred_fastest_s or 0.0), 1
        i_best = int(np.argmin(gaps)) + 1
        best = float(preferred_fastest_s) if preferred_fastest_s else float(gaps[i_best - 1])
        return total, best, i_best
    if preferred_fastest_s is not None:
        i_best, _gap = min(
            flying, key=lambda p: abs(p[1] - float(preferred_fastest_s))
        )
        return total, float(preferred_fastest_s), int(i_best)
    i_best, gap_best = min(flying, key=lambda p: p[1])
    return total, float(gap_best), int(i_best)


def count_ldx_beacons(ldx_path: Path | str) -> int:
    text = Path(ldx_path).read_text(encoding="utf-8", errors="replace")
    return text.count('ClassName="BCN"')


def ldx_fastest_lap_label(ld_path: Path | str) -> str:
    """``Lap N`` from .ldx Details / ``.vre_fastest_lap`` sidecar (default Lap 1)."""
    ld_path = Path(ld_path)
    side = Path(str(ld_path) + ".vre_fastest_lap")
    if side.is_file():
        val = side.read_text(encoding="ascii", errors="replace").strip()
        if val.startswith("Lap "):
            return val
        if val.isdigit():
            return f"Lap {val}"
    ldx = Path(str(ld_path) + "x")
    if ldx.is_file():
        import re

        m = re.search(r'Id="Fastest Lap" Value="(\d+)"', ldx.read_text(encoding="utf-8", errors="replace"))
        if m:
            return f"Lap {m.group(1)}"
    return "Lap 1"


def write_ld_from_channels(
    channels: Sequence[Dict[str, Any]],
    out_path: Path | str,
    *,
    driver: str = "VRE",
    vehicle: str = "rpy",
    venue: str = "ACC",
    comment: str = "ACC .rpy decode",
    beacon_times_s: Optional[Sequence[float]] = None,
    preferred_fastest_s: Optional[float] = None,
) -> Path:
    _ensure_ldparser()
    from ldparser import ldChan, ldData, ldEvent, ldHead  # type: ignore

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not channels:
        raise ValueError("no channels")

    meta_size = struct.calcsize(ldChan.fmt)
    head_size = struct.calcsize(ldHead.fmt)
    event_size = struct.calcsize(ldEvent.fmt)
    event_ptr = head_size
    meta_ptr0 = head_size + event_size
    data_ptr0 = meta_ptr0 + len(channels) * meta_size

    channs: list = []
    meta_ptr = meta_ptr0
    data_ptr = data_ptr0
    prev = 0
    for i, c in enumerate(channels):
        arr = np.asarray(c["data"], dtype=np.float32)
        next_ptr = meta_ptr + meta_size if i < len(channels) - 1 else 0
        chan = ldChan(
            None,
            meta_ptr,
            prev,
            next_ptr,
            data_ptr,
            len(arr),
            np.float32,
            int(round(float(c.get("freq") or 50))),
            0,
            1,
            1,
            0,
            str(c["name"])[:32],
            str(c.get("short_name") or c["name"])[:8],
            str(c.get("unit") or "")[:12],
        )
        chan._data = arr
        channs.append(chan)
        prev = meta_ptr
        meta_ptr = next_ptr if next_ptr else meta_ptr + meta_size
        data_ptr += arr.nbytes

    event = ldEvent("VRE", "1", comment[:1023], 0, None)
    head = ldHead(
        meta_ptr0,
        data_ptr0,
        event_ptr,
        event,
        driver[:64],
        vehicle[:64],
        venue[:64],
        datetime.datetime.now(),
        comment[:64],
    )
    ld = ldData(head, channs)
    ld.write(str(out_path))
    write_ldx_beacons(
        out_path,
        beacon_times_s=beacon_times_s,
        preferred_fastest_s=preferred_fastest_s,
    )
    return out_path


# Motec i2 1.1.6 on this install treats our rpy-built .ldx BCN Time as 1e6
# ticks/sec (not ACC's 1e7). Empirically: 1e7 → 10x lap times (21:50 vs 2:11);
# 1e6 → correct seconds. Keep ACC-style scientific formatting.
_MOTEC_BCN_TICKS_PER_SEC = 1_000_000.0


def write_ldx_beacons(
    ld_path: Path | str,
    beacon_times_s: Optional[Sequence[float]] = None,
    *,
    overwrite: bool = True,
    preferred_fastest_s: Optional[float] = None,
) -> Path:
    """Write companion .ldx BCN markers (Motec ticks/sec — see module const).

    Pass ``beacon_times_s`` from ``beacon_times_from_timer_laps`` (arr24 S/F
    crossings). Without times, loads ``.vre_beacons`` if present; otherwise a
    2-beacon session span (one mega-lap) — only for stubs / missing timer.
    """
    _ensure_ldparser()
    from ldparser import ldData  # type: ignore

    ld_path = Path(ld_path)
    ldx = Path(str(ld_path) + "x")
    data = ldData.fromfile(str(ld_path))
    names = list(data)
    # Prefer Motec sample-time (n/freq). Optional TIME channel is VRE-only and
    # must not drive beacon ticks (causes ~10x lap times in i2).
    ch0 = data[names[0]]
    n = int(len(ch0.data))
    freq = float(ch0.freq) or 50.0
    t_min, t_max = 0.0, (n - 1) / freq if n > 1 else 1.0
    side_beacons = Path(str(ld_path) + ".vre_beacons")
    # If caller already wrote Motec-relative beacons, keep their span for clamp.

    times: Optional[List[float]] = None
    if beacon_times_s is not None:
        times = [float(x) for x in beacon_times_s]
    else:
        side = Path(str(ld_path) + ".vre_beacons")
        if side.is_file():
            times = [
                float(x)
                for x in side.read_text(encoding="ascii", errors="replace").split()
                if x.strip()
            ]
        elif ldx.is_file() and not overwrite and count_ldx_beacons(ldx) >= 3:
            return ldx

    if preferred_fastest_s is None:
        ft_side = Path(str(ld_path) + ".vre_fastest_time")
        if ft_side.is_file():
            try:
                preferred_fastest_s = float(
                    ft_side.read_text(encoding="ascii", errors="replace").strip()
                )
            except ValueError:
                preferred_fastest_s = None

    if not times or len(times) < 2:
        times = [t_min, t_max]

    # Clamp + dedupe
    cleaned: List[float] = []
    for raw in times:
        tt = max(t_min, min(t_max, float(raw)))
        if not cleaned or (tt - cleaned[-1]) >= 0.05:
            cleaned.append(tt)
    if len(cleaned) < 2:
        cleaned = [t_min, t_max]

    total, fastest_s, fastest_idx = _flying_gap_stats(
        cleaned, preferred_fastest_s=preferred_fastest_s
    )
    marker_lines = []
    for i, sec in enumerate(cleaned):
        tick = f"{sec * _MOTEC_BCN_TICKS_PER_SEC:.17e}"
        marker_lines.append(
            f'     <Marker Version="100" ClassName="BCN" Name="{i}, id=99" '
            f'Flags="13" Time="{tick}" />'
        )
    markers = "\r\n".join(marker_lines)
    xml = (
        '<?xml version="1.0"?>\r\n'
        '<LDXFile Locale="English_United States.1252" DefaultLocale="C" Version="1.6">\r\n'
        " <Layers>\r\n"
        '  <Layer LayerName="">\r\n'
        "   <MarkerBlock>\r\n"
        '    <MarkerGroup Name="Beacons">\r\n'
        f"{markers}\r\n"
        "    </MarkerGroup>\r\n"
        "   </MarkerBlock>\r\n"
        "  </Layer>\r\n"
        "  <Details>\r\n"
        f'   <String Id="Total Laps" Value="{total}"/>\r\n'
        f'   <String Id="Fastest Time" Value="{format_motec_lap_time(fastest_s)}"/>\r\n'
        f'   <String Id="Fastest Lap" Value="{fastest_idx}"/>\r\n'
        "  </Details>\r\n"
        " </Layers>\r\n"
        "</LDXFile>\r\n"
    )
    ldx.write_bytes(xml.encode("utf-8"))
    Path(str(ld_path) + ".vre_beacons").write_text(
        "\n".join(f"{x:.6f}" for x in cleaned) + "\n", encoding="ascii"
    )
    Path(str(ld_path) + ".vre_fastest_lap").write_text(
        f"Lap {fastest_idx}\n", encoding="ascii"
    )
    Path(str(ld_path) + ".vre_fastest_time").write_text(
        f"{fastest_s:.6f}\n", encoding="ascii"
    )
    return ldx


def motec_outing_key(ld_path: Path | str) -> Optional[str]:
    """Motec Outings Range.Id suffix: ``5:45:36 AM, ACC rpy Tomato ``."""
    _ensure_ldparser()
    try:
        from ldparser import ldData  # type: ignore

        head = ldData.fromfile(str(ld_path)).head
        dt = head.datetime
        hour = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        t = f"{hour}:{dt.minute:02d}:{dt.second:02d} {ampm}"
        # Trailing space matches what Motec writes into workspaces.
        return f"{t}, {head.short_comment} "
    except Exception:
        return None


def write_driver_ld(
    decoded: Dict[str, Any],
    out_path: Path | str,
    *,
    label: str,
    track_grip: Optional[float] = None,
    steering_lock_deg: float = 400.0,
    electronics: Optional[Dict[str, Any]] = None,
    venue: str = "ACC",
    beacon_times_s: Optional[Sequence[float]] = None,
    preferred_fastest_s: Optional[float] = None,
) -> Path:
    return write_ld_from_channels(
        channels_from_decoded(
            decoded,
            prefix="",
            track_grip=track_grip,
            steering_lock_deg=steering_lock_deg,
            electronics=electronics,
        ),
        out_path,
        driver=label[:64],
        venue=venue[:64],
        comment=f"ACC rpy {label}",
        beacon_times_s=beacon_times_s,
        preferred_fastest_s=preferred_fastest_s,
    )


def write_overlay_ld(
    drivers: Sequence[Tuple[str, Dict[str, Any]]],
    out_path: Path | str,
    *,
    track_grip: Optional[float] = None,
    steering_lock_deg: float = 400.0,
    electronics: Optional[Dict[str, Any]] = None,
) -> Path:
    all_ch: List[Dict[str, Any]] = []
    labels: List[str] = []
    for label, decoded in drivers:
        slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:12]
        labels.append(slug)
        all_ch.extend(
            channels_from_decoded(
                decoded,
                prefix=slug,
                track_grip=track_grip,
                steering_lock_deg=steering_lock_deg,
                electronics=electronics,
            )
        )
    return write_ld_from_channels(
        all_ch,
        out_path,
        driver="+".join(labels)[:64],
        comment=f"overlay: {', '.join(labels)}",
    )
