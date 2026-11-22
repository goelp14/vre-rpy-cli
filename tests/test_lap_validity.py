"""Lap validity from the arr24 lap-timer snapshot (fixture-gated)."""

from pathlib import Path

from _fixtures import fixture_or_missing
import pytest

from vre_rpy.lap_validity import (
    attach_validity_to_segments,
    best_valid_flying_time_s,
    build_labeled_laps,
    decode_lap_validity,
    order_drivers_main_ref,
)

TOMATO = fixture_or_missing("ACC_261118-203124_O_ferrari_296_gt3_spa.rpy")
ERIK = fixture_or_missing("ACCReplay_240418-223443_Q_Spa_ERIK.rpy")


def _synthetic():
    import struct

    def rec(lap, ref, valid, t):
        return struct.pack("<IIBBBBI", lap, ref, valid, 0, 0, 0, 746) + struct.pack("<d", t)

    rows = []
    t = 0.0
    for ms in range(0, 100000, 250):  # valid lap, cut at 40 s
        rows.append(rec(ms, 0x7FFFFFFF, 1 if ms < 40000 else 0, t))
        t += 0.25
    rows.append(rec(5, 0x7FFFFFFF, 1, t))
    rows.append(rec(100000, 0, 0, t))  # marker
    rows.append(rec(12, 0x7FFFFFFF, 1, t))
    for ms in range(20, 100000, 250):  # clean lap
        rows.append(rec(ms, 0x7FFFFFFF, 1, t))
        t += 0.25
    rows.append(rec(5, 0x7FFFFFFF, 1, t))
    rows.append(rec(100010, 0, 0, t))
    rows.append(rec(12, 0x7FFFFFFF, 1, t))
    return b"".join(rows)


def test_synthetic_cut_and_clean():
    laps = [l for l in decode_lap_validity(_synthetic()) if l["flying_candidate"]]
    assert [l["valid"] for l in laps] == [False, True]
    assert laps[0]["invalid_at_lap_ms"] == 40000


def test_attach_to_segments():
    laps = [
        {"flying_candidate": True, "lap_ms": 100000, "valid": False, "invalid_at_lap_ms": 40000},
        {"flying_candidate": True, "lap_ms": 100000, "valid": True, "invalid_at_lap_ms": None},
    ]
    segs = [{"n_samples": 2500}, {"n_samples": 2500}]
    out = attach_validity_to_segments(segs, laps)
    assert [s["valid_flag"] for s in out] == [False, True]


@pytest.mark.skipif(not ERIK.is_file(), reason="Ref fixture missing")
def test_erik_best_lap_valid():
    from vre_rpy.lap_validity import decode_rpy_lap_validity

    laps = decode_rpy_lap_validity(ERIK)["laps"]
    by = {round(l["lap_ms"] / 1000, 1): l for l in laps if l["flying_candidate"]}
    assert by[135.4]["valid"] is True
    assert by[137.0]["valid"] is False


@pytest.mark.skipif(not TOMATO.is_file(), reason="Main fixture missing")
def test_tomato_best_lap_valid():
    from vre_rpy.lap_validity import decode_rpy_lap_validity

    laps = [l for l in decode_rpy_lap_validity(TOMATO)["laps"] if l["flying_candidate"]]
    best = min(laps, key=lambda l: l["lap_ms"])
    assert best["lap_ms"] // 1000 == 139 and best["valid"] is True
    assert sum(not l["valid"] for l in laps) >= 4


def test_build_labeled_laps_fastest_ignores_invalid_flyer():
    # Faster Motec gap is invalid; slower gap is valid — fastest must be Lap 2.
    timer = [
        {
            "session_start_s": 0.0,
            "session_end_s": 130.0,
            "lap_ms": 130_000,
            "completed": True,
            "flying_candidate": True,
            "valid": False,
            "invalid_at_lap_ms": 40_000,
        },
        {
            "session_start_s": 130.0,
            "session_end_s": 269.072,
            "lap_ms": 139_072,
            "completed": True,
            "flying_candidate": True,
            "valid": True,
            "invalid_at_lap_ms": None,
        },
    ]
    beacons = [0.0, 130.0, 269.072]
    labeled = build_labeled_laps(timer, beacons, venue="Spa")
    assert labeled["fastest_valid_only"] is True
    assert labeled["fastest_motec_lap"] == 2
    assert labeled["fastest_time_s"] == pytest.approx(139.072, abs=0.01)


def test_order_drivers_main_ref_picks_fastest_valid():
    tomato = {
        "label": "Main",
        "laps": {
            "laps": [
                {
                    "flying_candidate": True,
                    "valid": True,
                    "completed": True,
                    "duration_s": 139.072,
                },
                {
                    "flying_candidate": True,
                    "valid": False,
                    "completed": True,
                    "duration_s": 120.0,  # faster but invalid — must not win Ref
                },
            ]
        },
    }
    erik = {
        "label": "Ref",
        "laps": {
            "laps": [
                {
                    "flying_candidate": True,
                    "valid": True,
                    "completed": True,
                    "duration_s": 135.425,
                }
            ]
        },
    }
    # Input order Ref first — still Main=Main (slower valid), Ref=Ref
    out = order_drivers_main_ref([erik, tomato])
    assert out["ref_reason"] == "fastest_valid_flying_lap"
    assert out["ref_label"] == "Ref"
    assert out["main_label"] == "Main"
    assert [d["label"] for d in out["drivers"][:2]] == ["Main", "Ref"]
    assert out["ref_valid_time_s"] == pytest.approx(135.425)
    assert best_valid_flying_time_s(tomato["laps"]) == pytest.approx(139.072)


@pytest.mark.skipif(
    not (TOMATO.is_file() and ERIK.is_file()), reason="Spa fixtures missing"
)
def test_spa_pair_erik_is_motec_ref():
    from vre_rpy.lap_validity import decode_rpy_lap_validity

    def pack(label, path):
        laps = decode_rpy_lap_validity(path)["laps"]
        return {
            "label": label,
            "laps": {
                "laps": [
                    {
                        "flying_candidate": l["flying_candidate"],
                        "valid": l["valid"],
                        "completed": l["completed"],
                        "duration_s": l["lap_ms"] / 1000.0,
                    }
                    for l in laps
                ]
            },
        }

    out = order_drivers_main_ref([pack("Main", TOMATO), pack("Ref", ERIK)])
    assert out["ref_label"] == "Ref"
    assert out["main_label"] == "Main"
    assert out["ref_valid_time_s"] == pytest.approx(135.425, abs=0.05)
