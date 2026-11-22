"""Motec .ldx lap beacons from arr24 timer — not one mega-lap."""

from __future__ import annotations

from _fixtures import fixture_or_missing
import re
from pathlib import Path

import numpy as np
import pytest

from vre_rpy.rpy_motec_ld import (
    beacon_times_from_timer_laps,
    format_motec_lap_time,
    ldx_fastest_lap_label,
    motec_venue_from_track,
    write_ldx_beacons,
    write_ld_from_channels,
)

SPA = fixture_or_missing("ACC_261118-203124_O_ferrari_296_gt3_spa.rpy")
ERIK = fixture_or_missing("ACCReplay_240418-223443_Q_Spa_ERIK.rpy")


def assert_motec_lap_matches_fastest(ld: Path) -> None:
    """Motec numbers [log start, marker0] as Lap 1, so Lap N spans
    markers[N-2]..markers[N-1] (marker[-1] = 0). That span must be the fastest."""
    text = Path(str(ld) + "x").read_text(encoding="utf-8")
    ticks = [float(x) / 1e6 for x in re.findall(r'ClassName="BCN"[^>]*Time="([^"]+)"', text)]
    assert ticks[0] > 0.0, "marker at log start creates an empty Motec Lap 1"
    n = int(ldx_fastest_lap_label(ld).split()[1])
    edges = [0.0] + ticks
    span = edges[n] - edges[n - 1]
    m, s = re.search(r'Id="Fastest Time" Value="(\d+):([\d.]+)"', text).groups()
    assert span == pytest.approx(int(m) * 60 + float(s), abs=0.05)


def test_venue_maps_spa_to_motec_mt2_name():
    assert motec_venue_from_track("spa_acc") == "Spa"
    assert motec_venue_from_track("driver", filename_hints={"track": "Spa_DRIVER"}) == "Spa"
    assert motec_venue_from_track("monza_acc") == "monza"
    erik_hints = {
        "car": "Spa",
        "track": "DRIVER",
        "stem": "ACCReplay_240418-223443_Q_Spa_DRIVER",
    }
    assert motec_venue_from_track("erik", filename_hints=erik_hints) == "Spa"


def test_beacon_times_split_flying_laps_not_mega_session():
    laps = [
        {"session_start_s": 0.0, "session_end_s": 100.0},
        {"session_start_s": 100.0, "session_end_s": 240.0},
        {"session_start_s": 240.0, "session_end_s": 379.0},
    ]
    b = beacon_times_from_timer_laps(laps, t_min=0.0, t_max=400.0)
    assert b[0] == 0.0
    assert len(b) == 4
    gaps = [b[i + 1] - b[i] for i in range(len(b) - 1)]
    assert gaps == pytest.approx([100.0, 140.0, 139.0])
    assert format_motec_lap_time(139.072) == "2:19.072"


def test_beacon_times_shift_session_clock_onto_motec_axis():
    """Downloaded-replay style: session clock starts ~1714s; Motec log axis starts at 0."""
    laps = [
        {"session_start_s": 1714.0, "session_end_s": 1764.4},
        {"session_start_s": 1764.4, "session_end_s": 1900.0},
    ]
    b = beacon_times_from_timer_laps(
        laps, t_min=1714.0, t_max=1900.0, motec_t0=1714.0
    )
    assert b[0] == pytest.approx(0.0, abs=0.01)
    assert b[1] == pytest.approx(50.4, abs=0.01)
    assert b[-1] == pytest.approx(186.0, abs=0.01)
    assert max(b) < 200.0  # must not leave Motec's ~186s log span


def test_build_labeled_laps_matches_motec_indices():
    from vre_rpy.lap_validity import build_labeled_laps

    timer = [
        {
            "session_start_s": 0.0,
            "session_end_s": 100.0,
            "lap_ms": 100000,
            "completed": False,
            "flying_candidate": False,
            "valid": True,
            "invalid_at_lap_ms": None,
        },
        {
            "session_start_s": 100.0,
            "session_end_s": 239.072,
            "lap_ms": 139072,
            "completed": True,
            "flying_candidate": True,
            "valid": True,
            "invalid_at_lap_ms": None,
        },
    ]
    beacons = [0.0, 100.0, 239.072]
    labeled = build_labeled_laps(
        timer, beacons, venue="Spa", preferred_fastest_s=139.072
    )
    assert labeled["total_laps"] == 2
    assert labeled["fastest_motec_lap"] == 2
    assert labeled["laps"][1]["role"] == "flying"
    assert labeled["laps"][1]["label"] == "Lap 2"
    assert labeled["venue"] == "Spa"


@pytest.mark.skipif(not SPA.is_file(), reason="Spa .rpy fixture missing")
def test_tomato_ldx_has_many_beacons_matching_timer(tmp_path: Path):
    from vre_rpy.arr30_decode import decode_rpy_inputs
    from vre_rpy.lap_validity import decode_rpy_lap_validity
    from vre_rpy.rpy_motec_ld import write_driver_ld

    decoded = decode_rpy_inputs(SPA)
    timer = decode_rpy_lap_validity(SPA)
    t = np.asarray(decoded["time_s"], dtype=np.float64)
    beacons = beacon_times_from_timer_laps(
        timer["laps"], t_min=float(t[0]), t_max=float(t[-1]), motec_t0=float(t[0])
    )
    assert len(beacons) >= 10, f"expected multi-lap beacons, got {len(beacons)}"

    out = tmp_path / "Main.ld"
    write_driver_ld(
        decoded,
        out,
        label="Main",
        venue="Spa",
        beacon_times_s=beacons,
    )
    ldx = Path(str(out) + "x")
    text = ldx.read_text(encoding="utf-8")
    n_bcn = text.count('ClassName="BCN"')
    assert n_bcn >= 10
    total = int(re.search(r'Id="Total Laps" Value="(\d+)"', text).group(1))
    assert total >= 9
    fastest = re.search(r'Id="Fastest Time" Value="([^"]+)"', text).group(1)
    # reference fixture best flying ≈ 2:19.072
    assert fastest.startswith("2:1")
    assert_motec_lap_matches_fastest(out)
    # Bundle must not smash multi-lap ldx
    write_ldx_beacons(out, overwrite=False)
    assert Path(str(out) + "x").read_text(encoding="utf-8").count('ClassName="BCN"') == n_bcn


@pytest.mark.skipif(not ERIK.is_file(), reason="reference .rpy fixture missing")
def test_erik_fastest_lap_is_215425(tmp_path: Path):
    from vre_rpy.arr30_decode import decode_rpy_inputs
    from vre_rpy.lap_validity import decode_rpy_lap_validity
    from vre_rpy.rpy_motec_ld import write_driver_ld

    decoded = decode_rpy_inputs(ERIK)
    timer = decode_rpy_lap_validity(ERIK)
    t = np.asarray(decoded["time_s"], dtype=np.float64)
    beacons = beacon_times_from_timer_laps(
        timer["laps"], t_min=float(t[0]), t_max=float(t[-1]), motec_t0=float(t[0])
    )
    flying_ms = [
        int(l["lap_ms"])
        for l in timer["laps"]
        if l.get("flying_candidate") and l.get("completed")
    ]
    preferred = min(flying_ms) / 1000.0
    out = tmp_path / "Ref.ld"
    write_driver_ld(
        decoded,
        out,
        label="Ref",
        venue="Spa",
        beacon_times_s=beacons,
        preferred_fastest_s=preferred,
    )
    text = Path(str(out) + "x").read_text(encoding="utf-8")
    fastest = re.search(r'Id="Fastest Time" Value="([^"]+)"', text).group(1)
    # Official 2:15.425 from arr24 lap_ms
    assert fastest == "2:15.425"
    assert_motec_lap_matches_fastest(out)
    assert preferred == pytest.approx(135.425, abs=0.001)
    gaps = [beacons[i + 1] - beacons[i] for i in range(len(beacons) - 1)]
    assert min(g for g in gaps if 60 < g < 600) == pytest.approx(135.425, abs=0.05)


def test_write_ldx_fallback_two_beacons_only_without_timer(tmp_path: Path):
    n = 100
    t = np.linspace(0.0, 99.0, n, dtype=np.float32)
    out = tmp_path / "stub.ld"
    write_ld_from_channels(
        [{"name": "TIME", "short_name": "TIME", "unit": "s", "freq": 10, "data": t}],
        out,
        venue="Spa",
    )
    text = Path(str(out) + "x").read_text(encoding="utf-8")
    assert text.count('ClassName="BCN"') == 1
