"""Motec one-click workspace writer — Workbooks XRefs + launcher + channel map."""

from __future__ import annotations

from pathlib import Path

from _fixtures import fixture_or_missing
import pytest

from vre_rpy.motec_workspace import (
    ACC_COMPARE_CHANNELS,
    build_compare_workbook_xml,
    build_overlay_workbook_xml,
    to_motec_windows_path,
    write_motec_workspace_bundle,
)


def test_to_motec_windows_path_wsl_unc():
    p = Path("/home/user/project/exports/x/motec/Main.ld")
    unc = to_motec_windows_path(p)
    assert unc.startswith("\\\\wsl$\\")
    assert unc.endswith("\\motec\\Main.ld")
    assert "/" not in unc.replace("\\\\wsl$\\", "")


def test_compare_workbook_uses_acc_channels_not_prefixes():
    xml = build_compare_workbook_xml()
    assert 'Name="VRE Compare"' in xml
    assert 'Active="Track Map"' in xml
    assert 'Name="Track Map"' in xml
    assert "MoTeC.TrackReport" in xml
    assert 'ShowSectLabels="1"' in xml
    for ch in ACC_COMPARE_CHANNELS:
        assert f'Trace Id="{ch}"' in xml, ch
    assert "Main_" not in xml
    assert "Ref_" not in xml


def test_overlay_workbook_prefixes_drivers():
    xml = build_overlay_workbook_xml(["Main", "Ref"])
    assert 'Name="VRE Overlay"' in xml
    assert 'Trace Id="Main_SPEED_KMH"' in xml
    assert 'Trace Id="Ref_THROTTLE"' in xml


def test_write_bundle_registers_workbooks_and_launcher(tmp_path: Path):
    motec = tmp_path / "motec"
    motec.mkdir()
    # stub ld files (empty is fine for path wiring)
    main = motec / "Main.ld"
    ref = motec / "Ref.ld"
    overlay = motec / "overlay.ld"
    main.write_bytes(b"ld")
    ref.write_bytes(b"ld")
    overlay.write_bytes(b"ld")

    meta = write_motec_workspace_bundle(
        motec_dir=motec,
        drivers=[
            {"label": "Main", "ld": main},
            {"label": "Ref", "ld": ref},
        ],
        overlay_ld=overlay,
        workspace_name="VRE_Export",
    )

    ws = Path(meta["workspace_dir"])
    assert ws.is_dir()
    wsp = ws / "Workspace.i2wsp"
    text = wsp.read_text(encoding="utf-8")
    # THE bug: Workbooks must list XRefs or Motec shows blank Layouts
    assert 'Active="VRE Compare"' in text
    assert r'XRef="Workbooks\VRE_Compare.i2wkb"' in text
    assert r'XRef="Workbooks\VRE_Overlay.i2wkb"' in text
    assert "Main.ld" in text
    assert "Ref.ld" in text
    assert "<Main " in text and "<Ref " in text
    assert "::::Lap 1" in text  # stub LDs have no multi-beacon .ldx yet
    assert "TelemetryEnabled" in text
    assert 'ExcludeUntrusted="0"' in text
    assert 'ExcludeUntrusted="1"' not in text
    assert (ws / "Track Maps").is_dir()
    maths = (ws / "Internal Maths.xml").read_text(encoding="utf-8")
    assert "MoTeC.CircuitMaths" in maths
    assert "[SPEED;" in maths or "[SPEED]" in maths
    assert 'Params="4:0;' in maths  # dual-wheel pair OFF (ACC Workspace V1)
    assert "Ground Speed" in maths
    overrides = (ws / "Channels" / "Overrides.xml").read_text(encoding="utf-8")
    assert 'Id="SPEED"' in overrides and 'DisplayUnit="km/h"' in overrides

    assert (ws / "Workbooks" / "VRE_Compare.i2wkb").is_file()
    assert (ws / "Workbooks" / "VRE_Overlay.i2wkb").is_file()
    assert (motec / "Open_VRE.bat").is_file()
    assert (motec / "Open_VRE.ps1").is_file()
    assert (motec / "OPEN_WORKSPACE.txt").is_file()
    ps1 = (motec / "Open_VRE.ps1").read_text(encoding="utf-8-sig")
    assert "VRE_Export" in ps1
    assert "$PSScriptRoot" in ps1
    assert "-w" in ps1 and "-l" in ps1
    # Skip WebView2 Choose-a-Workspace splash + allow rpy LDs
    assert "LoadPrevWorkspace" in ps1
    assert "RecentFiles" in ps1
    assert "UNC source detected" in ps1
    assert "ExcludeUntrusted" in ps1
    assert "SelectFastestOnLoad" in ps1
    assert "Fastest-Lap" in ps1
    assert "vre_fastest_lap" in ps1
    assert "vre_outing" in ps1
    # Main + Ref auto-load: LoadPrevData=1 and -w only (-l drops the Ref log)
    assert "$launchArgs = @('-w', $DestWorkspace)" in ps1
    assert "'LoadPrevData'" in ps1
    assert "'-l'" not in ps1
    assert "$mainLd" in ps1
    assert "fastest VALID" in ps1
    assert "<Ref Range.Id" in ps1
    assert "overlay.ld" in ps1
    # Windows PowerShell 5.1 breaks on UTF-8 emdash without BOM — keep ASCII
    assert "\u2014" not in ps1
    assert "$args =" not in ps1  # do not shadow automatic $args
    assert meta["launcher_bat"].endswith("Open_VRE.bat")
    assert meta["active_workbook"] == "VRE Compare"
    assert "track_maps" in meta
    # Do NOT Ctrl+O (Motec treats it as Open Folder — breaks Ref load)
    assert "SendKeys" not in ps1
    assert "Set-Clipboard" not in ps1
    assert (ws / "Workbooks" / "VRE_Compare.i2wkb").read_text(encoding="utf-8").count(
        "Track Map"
    ) >= 1


@pytest.mark.skipif(
    not (
        fixture_or_missing("ACC_261118-203124_O_ferrari_296_gt3_spa.rpy")
    ).is_file(),
    reason="Spa .rpy fixture missing",
)
def test_export_rpy_analysis_emits_launcher(tmp_path: Path):
    from vre_rpy.export_rpy_analysis import run

    spa = (
        fixture_or_missing("ACC_261118-203124_O_ferrari_296_gt3_spa.rpy")
    )
    out = tmp_path / "export"
    # two labels → Main+Overlay wiring
    summary = run([(spa, "Main"), (spa, "Ref")], out)
    assert (out / "motec" / "Open_VRE.bat").is_file()
    assert (out / "motec" / "Open_VRE.ps1").is_file()
    wsp = out / "motec" / "VRE_Export" / "Workspace.i2wsp"
    # workspace folder name may vary — find it
    workspaces = list((out / "motec").glob("*/Workspace.i2wsp"))
    assert workspaces, "expected Motec workspace under motec/"
    text = workspaces[0].read_text(encoding="utf-8")
    assert "VRE_Compare.i2wkb" in text
    assert summary.get("motec_workspace")
    assert summary["motec_workspace"]["launcher_bat"]
    # channel map sanity on written ld
    import sys

    sys.path.insert(0, "/tmp/ldparser")
    from ldparser import ldData

    names = list(ldData.fromfile(str(out / "motec" / "Main.ld")))
    for need in ACC_COMPARE_CHANNELS:
        if need.startswith("VRE_"):
            continue  # optional presence checked loosely
        assert need in names, need
