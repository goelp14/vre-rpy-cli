"""verify_motec_export — rpy lap times must match .ldx beacon gaps (no guessing)."""

from __future__ import annotations

from pathlib import Path

from _fixtures import fixture_or_missing
import pytest

from vre_rpy.motec_workspace import ACC_INTERNAL_MATHS_XML
from vre_rpy.verify_motec_export import (
    check_internal_maths,
    check_lap_times,
    check_mt2,
)

TOMATO_RPY = fixture_or_missing("ACC_261118-203124_O_ferrari_296_gt3_spa.rpy")
ERIK_RPY = fixture_or_missing("ACCReplay_240418-223443_Q_Spa_ERIK.rpy")


def test_tomato_rpy_matches_ldx_fastest_valid(spa_pair_export):
    report = check_lap_times(TOMATO_RPY, spa_pair_export / "motec" / "Main.ldx")
    assert report["best_valid_s"] is not None
    assert abs(report["best_valid_s"] - 139.072) < 0.002
    assert report["ldx_fastest_time"] == "2:19.072"
    assert report["fastest_time_ok"]
    assert report["ok"], report["misses"]


def test_erik_rpy_matches_ldx_fastest_valid(spa_pair_export):
    report = check_lap_times(ERIK_RPY, spa_pair_export / "motec" / "Ref.ldx")
    assert report["best_valid_s"] is not None
    assert abs(report["best_valid_s"] - 135.425) < 0.002
    assert report["ldx_fastest_time"] == "2:15.425"
    assert report["fastest_time_ok"]
    assert report["ok"], report["misses"]


def test_internal_maths_requires_speed(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    bad = (
        '<?xml version="1.0"?>\r\n'
        '<Maths Id="Internal" Flags="196">\r\n'
        " <MathItems>\r\n"
        '  <MathPlugin ProgId="MoTeC.CircuitMaths.1.3" '
        'Params="4:1;Wheel Speed FL;Wheel Speed FR:1:[Ground Speed;'
        'Wheel Speed FL]:0;GPS Speed:0;G Force Long;-1;0;0:0;'
        'Corr Speed;Lap Distance:0;5:1;1:0"/>\r\n'
        " </MathItems>\r\n"
        "</Maths>\r\n"
    )
    (ws / "Internal Maths.xml").write_text(bad, encoding="utf-8")
    assert check_internal_maths(ws)["ok"] is False

    (ws / "Internal Maths.xml").write_text(ACC_INTERNAL_MATHS_XML, encoding="utf-8")
    good = check_internal_maths(ws)
    assert good["ok"] is True
    assert good["has_SPEED"] is True
    assert good["dual_wheel_pair_enabled"] is False


def test_mt2_size_gate(tmp_path: Path):
    src = tmp_path / "Spa.mt2"
    # Minimal fake MotecTrack header + Points/Data/SectionGroups markers
    blob = (
        b"\x0c\x00\x00\x00MoTeCTrackV2"
        + b"\x00" * 40
        + b"Points"
        + b"\x00" * 8
        + b"Data"
        + b"\x00" * 8
        + b"SectionGroups"
        + b"\x00" * 20
    )
    src.write_bytes(blob)
    dest = tmp_path / "out.mt2"
    dest.write_bytes(blob)
    assert check_mt2(dest, source=src)["ok"] is True
    dest.write_bytes(blob + b"X")
    assert check_mt2(dest, source=src)["ok"] is False
