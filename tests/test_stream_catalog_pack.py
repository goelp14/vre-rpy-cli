"""Stream catalog + agent pack smoke tests."""

from __future__ import annotations

from _fixtures import fixture_or_missing
import json
from pathlib import Path

import pytest

SPA = fixture_or_missing("ACC_261118-203124_O_ferrari_296_gt3_spa.rpy")


@pytest.mark.skipif(not SPA.is_file(), reason="Spa .rpy fixture missing")
def test_agent_pack_writes_manifest(tmp_path: Path):
    from vre_rpy.export_agent_pack import build_pack

    man = build_pack(SPA, tmp_path / "pack", motec_path=None)
    assert (tmp_path / "pack" / "manifest.json").is_file()
    assert (tmp_path / "pack" / "stream_catalog.json").is_file()
    assert (tmp_path / "pack" / "session.json").is_file()
    data = json.loads((tmp_path / "pack" / "manifest.json").read_text())
    assert data["schema"] == "vre.acc_rpy.agent_pack.v1"
    assert man["gates"]["RPY_TELEMETRY_GO"] is True
    streams = tmp_path / "pack" / data["outputs"]["streams_dir"]
    assert (streams / "lap_time_ms.json").is_file()
    assert (streams / "laps.json").is_file()
    laps_ai = json.loads((streams / "laps.json").read_text())
    assert laps_ai["status"] == "file_go"
    assert laps_ai["total_laps"] >= 2
    assert laps_ai["venue"] == "Spa"
    assert laps_ai["fastest_time_s"] == pytest.approx(139.072, abs=0.05)
    assert len(laps_ai["motec_beacons_s"]) >= 3
    assert (streams / "arr30_inputs.json").is_file()
    ped = json.loads((streams / "arr30_inputs.json").read_text())
    assert ped["status"] == "file_go"
    assert ped["count"] > 1000
    assert len(ped["throttle"]) > 100
    assert len(ped["steer_deg"]) > 100
    assert len(ped["gear"]) > 100
    assert len(ped["rpm"]) > 100
    assert len(ped["disc_temp"]["LF"]) > 100
    assert len(ped["tyre_load"]["RF"]) > 100
    assert (streams / "pose_track.json").is_file()
    pose = json.loads((streams / "pose_track.json").read_text())
    assert pose["status"] == "file_go"
    assert pose["count"] > 1000
    assert len(pose["spline"]) > 100
    assert (streams / "pending_runtime_streams.json").is_file()


@pytest.mark.skipif(not SPA.is_file(), reason="Spa .rpy fixture missing")
def test_export_rpy_analysis_writes_motec_and_ai(tmp_path: Path):
    from vre_rpy.export_rpy_analysis import run

    # Main-only for speed; overlay prefix covered by single-driver channels
    out = tmp_path / "export"
    summary = run([(SPA, "Main")], out)
    assert (out / "drivers.json").is_file()
    assert (out / "motec" / "overlay.ld").is_file()
    assert (out / "motec" / "Main.ld").is_file()
    assert (out / "motec" / "Open_VRE.bat").is_file()
    assert (out / "motec" / "VRE_Export" / "Workspace.i2wsp").is_file()
    wsp = (out / "motec" / "VRE_Export" / "Workspace.i2wsp").read_text(encoding="utf-8")
    assert r"Workbooks\VRE_Compare.i2wkb" in wsp
    assert (out / "motec_pace_setup.json").is_file()
    assert summary["motec_pace_setup"]["doctrine"]["reject"] == "more_stable_but_slower"
    assert summary["motec_workspace"]["active_workbook"] == "VRE Compare"
    assert "disc_temp" in summary["channels_file_go"]
    assert "tyre_load" in summary["channels_file_go"]
    for need in ("VRE_US_OS", "VRE_TC_EST", "VRE_ABS_EST", "VRE_TRACK_GRIP"):
        assert need in summary["motec_calc_channels"]

    import sys

    sys.path.insert(0, "/tmp/ldparser")
    from ldparser import ldData

    ld = ldData.fromfile(str(out / "motec" / "Main.ld"))
    assert ld.head.venue == "Spa"
    names = list(ld)
    for need in (
        "THROTTLE",
        "STEERANGLE",
        "RPMS",
        "G_LAT",
        "BRAKE_TEMP_LF",
        "WHEEL_SPEED_LF",
        "TYRE_LOAD_RR",
        "SLIP_RATIO_LR",
        "VRE_COAST",
    ):
        assert need in names, need

    ldx = (out / "motec" / "Main.ldx").read_text(encoding="utf-8")
    assert ldx.count('ClassName="BCN"') >= 10
    assert 'Id="Fastest Time" Value="2:19.072"' in ldx
    assert (out / "ai_series" / "Main_laps.json").is_file()
    ai_laps = json.loads((out / "ai_series" / "Main_laps.json").read_text())
    assert ai_laps["total_laps"] >= 10
    assert ai_laps["fastest_label"] == "Lap 12"
    assert summary["drivers"][0]["venue"] == "Spa"
    assert summary["drivers"][0]["motec_beacons"] >= 10
