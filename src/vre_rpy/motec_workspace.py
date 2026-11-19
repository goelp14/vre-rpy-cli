"""Generate Motec i2 Pro workspace + one-click launcher for ACC .rpy exports.

MoTeC requires Workspace.i2wsp to list Workbook XRefs. An empty
``<Workbooks/>`` yields the blank UI: "Please select a Worksheet from the
Layouts Window."

Two workbooks:
  - **VRE Compare** — standard ACC channel names; open Tomato.ld (Main) +
    Erik.ld (Overlay). This is the default one-click path.
  - **VRE Overlay** — ``Tomato_*`` / ``Erik_*`` prefixes for ``overlay.ld``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

# Channels Motec Compare worksheets must match rpy_motec_ld.write_driver_ld
ACC_COMPARE_CHANNELS: List[str] = [
    "SPEED_KMH",
    "SPEED",
    "THROTTLE",
    "BRAKE",
    "STEERANGLE",
    "GEAR",
    "RPMS",
    "G_LAT",
    "G_LON",
    "VRE_COAST",
    "VRE_PEDAL_GAP",
    "VRE_US_OS",
    "VRE_SPIN",
    "VRE_LOCK",
    "VRE_TC_ACT",
    "VRE_ABS_ACT",
    "VRE_TC_LEVEL",
    "VRE_ABS_LEVEL",
]

_PathLike = Union[str, Path]


def to_motec_windows_path(path: _PathLike) -> str:
    """Absolute path Motec on Windows can open (WSL → \\\\wsl$\\Distro\\...)."""
    p = Path(path).resolve()
    s = str(p)
    if re.match(r"^[A-Za-z]:[\\/]", s):
        return str(p).replace("/", "\\")
    if s.startswith("/mnt/") and len(s) > 6 and s[5].isalpha() and s[6] == "/":
        # /mnt/c/Users/... → C:\Users\...
        drive = s[5].upper()
        rest = s[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    if s.startswith("/"):
        distro = os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
        return "\\\\wsl$\\" + distro + s.replace("/", "\\")
    return str(p).replace("/", "\\")


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _graph_block(
    name: str,
    top: int,
    bottom: int,
    traces: Sequence[tuple[str, str, str]],
    *,
    active: Optional[str] = None,
) -> str:
    """One Motec graph pane. traces = [(id, unit, dps), ...]."""
    if not traces:
        raise ValueError("traces required")
    act = active or traces[0][0]
    lines = [
        f'      <Graph Name="{name}" ScaleMode="2" ScaleMin="0" ScaleMax="1">',
        f'       <Edges Mode="0" TopEdge="{top}" BottomEdge="{bottom}"/>',
        '       <Edges Mode="1" TopEdge="0" BottomEdge="1000"/>',
        "       <Lines>",
        '        <Line Index="0" Enabled="1" Value="0" Color="210,210,210"/>',
        '        <Line Index="1" Enabled="0" Value="0" Color="0,0,248"/>',
        '        <Line Index="2" Enabled="0" Value="0" Color="248,0,0"/>',
        "       </Lines>",
        f'       <Traces ActiveTrace="{act}">',
    ]
    for tid, unit, dps in traces:
        lines.append(
            f'        <Trace Id="{tid}" DisplayUnit="{unit}" DisplayDPS="{dps}" Visible="1"/>'
        )
    lines += ["       </Traces>", "      </Graph>"]
    return "\n".join(lines)


def _time_distance_worksheet(
    sheet_name: str,
    graphs: Sequence[str],
    *,
    activate: str,
) -> str:
    body = "\n".join(graphs)
    return f"""  <Worksheet Version="200" Name="{sheet_name}" LinkTo="Laps" DataDomain="1" LockByGPSTime="0">
   <Controls Active="Time/Distance [1]">
    <MoTeC.TimeDistance Version="100" Name="Time/Distance [1]" PosPC="0,0,1000,1000" ShowCaption="0" AutoCaption="0" LinkZoom="1" ModeId="0" ShowDataLegend="1" ShowColorLegend="1" TelemetryZoom="0" TraceLineWidth="1" TracePointSize="3" TraceStyle="0" LinkCursor="1" LinkDatum="1" ShowKeyValues="1" ShowKeyStats="1" KeyMode="0" KeyPos="1" ShowGridLines="1" ShowXAxis="1" ShowXAxisScrollBar="0" ShowYAxis="1" ShowYAxisScrollBar="1" ShowMarkers="1" ShowVSP="0" ShowDeviveEvents="1" ShowBands="1" BandGroup="Outings:Laps:Track Sections:Default" BandMode="1" OverlapMode="0" VariancePos="0.25" VarianceAutoScale="1">
     <Graphs ActivateChild="{activate}">
{body}
     </Graphs>
     <Cursor Version="1" Style="1" Snap="1"/>
    </MoTeC.TimeDistance>
   </Controls>
  </Worksheet>"""


def _track_map_worksheet() -> str:
    """Track Map panel: Time/Distance + TrackReport with section labels visible.

    Motec loads the venue .mt2 from Workspace Track Maps\\ (patched with real
    corner names). Cursor links so scrubbing data moves the car on track.
    """
    speed = _graph_block(
        "Speed",
        0,
        1000,
        [("SPEED_KMH", "km/h", "1"), ("SPEED", "m/s", "2")],
        active="SPEED_KMH",
    )
    return f"""  <Worksheet Version="200" Name="Track Map" LinkTo="Laps" DataDomain="1" LockByGPSTime="0">
   <Controls Active="Time/Distance [1]">
    <MoTeC.TimeDistance Version="100" Name="Time/Distance [1]" PosPC="0,0,1000,220" ShowCaption="0" AutoCaption="0" LinkZoom="1" ModeId="0" ShowDataLegend="1" ShowColorLegend="1" TelemetryZoom="0" TraceLineWidth="1" TracePointSize="3" TraceStyle="0" LinkCursor="1" LinkDatum="1" ShowKeyValues="1" ShowKeyStats="1" KeyMode="0" KeyPos="1" ShowGridLines="1" ShowXAxis="1" ShowXAxisScrollBar="0" ShowYAxis="1" ShowYAxisScrollBar="1" ShowMarkers="1" ShowVSP="0" ShowDeviveEvents="1" ShowBands="1" BandGroup="Outings:Laps:Track Sections:Default" BandMode="1" OverlapMode="0" VariancePos="0.25" VarianceAutoScale="1">
     <Graphs ActivateChild="Speed">
{speed}
     </Graphs>
     <Cursor Version="1" Style="1" Snap="1"/>
    </MoTeC.TimeDistance>
    <MoTeC.TrackReport Version="100" Name="Track Report [1]" PosPC="0,220,1000,1000" ShowCaption="0" AutoCaption="0" LinkZoom="0" ModeId="0" ShowDataLegend="0" ShowColorLegend="1" TelemetryZoom="0" ShowSectLabels="1" ShowSectMarks="1" LinkCursor="1">
     <TrackMapWindow>
      <SizeScheme>
       <SizeSchemeEntry Name="Cursor" INTValue="10"/>
       <SizeSchemeEntry Name="Detail" INTValue="2"/>
       <SizeSchemeEntry Name="Material" INTValue="6"/>
      </SizeScheme>
     </TrackMapWindow>
     <ContourChannel ID="SPEED_KMH" Enabled="1" ScaleMode="1" UnitsMode="0">
      <EvenWidthBandAxis PaletteMode="1" GradientMode="0" BandChanColor="244,244,0" AxisChanColor="244,244,0" Color1="64,64,255" Color2="255,64,64" ReversGradient="0" CentredBands="0" GreaterMaxBand="1" LessMinBand="1" Continuous="0" BandMode="1" DataMin="40" DataMax="280" BandWidth="24" BandCount="10">
       <ColorBand Flags="2" Min="28" Max="28" Color="64,64,255" Visible="1"/>
       <ColorBand Flags="7" Min="28" Max="52" Color="64,102,255" Visible="1"/>
       <ColorBand Flags="7" Min="52" Max="76" Color="64,174,255" Visible="1"/>
       <ColorBand Flags="7" Min="76" Max="100" Color="64,255,226" Visible="1"/>
       <ColorBand Flags="7" Min="100" Max="124" Color="64,255,117" Visible="1"/>
       <ColorBand Flags="7" Min="124" Max="148" Color="117,255,64" Visible="1"/>
       <ColorBand Flags="7" Min="148" Max="172" Color="226,255,64" Visible="1"/>
       <ColorBand Flags="7" Min="172" Max="196" Color="255,226,64" Visible="1"/>
       <ColorBand Flags="7" Min="196" Max="220" Color="255,174,64" Visible="1"/>
       <ColorBand Flags="15" Min="220" Max="244" Color="255,117,64" Visible="1"/>
       <ColorBand Flags="1" Min="244" Max="244" Color="255,64,64" Visible="1"/>
      </EvenWidthBandAxis>
     </ContourChannel>
     <Cursor Version="1" Style="1" Snap="1"/>
    </MoTeC.TrackReport>
    <MoTeC.TrackPos Version="100" Name="Track Position" PosPC="780,0,1000,220" ShowCaption="0" AutoCaption="0" LinkZoom="1" ModeId="0" ShowDataLegend="1" TelemetryZoom="0" ShowSectLabels="1" ShowSectMarks="1" LinkCursor="1">
     <TrackMapWindow>
      <SizeScheme>
       <SizeSchemeEntry Name="Cursor" INTValue="7"/>
       <SizeSchemeEntry Name="Detail" INTValue="1"/>
       <SizeSchemeEntry Name="Material" INTValue="1"/>
      </SizeScheme>
     </TrackMapWindow>
    </MoTeC.TrackPos>
   </Controls>
  </Worksheet>"""


def build_compare_workbook_xml() -> str:
    """Workbook for Main+Overlay on shared ACC channel names."""
    compare = _time_distance_worksheet(
        "Compare",
        [
            _graph_block(
                "Speed",
                0,
                350,
                [("SPEED_KMH", "km/h", "1"), ("SPEED", "m/s", "2")],
                active="SPEED_KMH",
            ),
            _graph_block("Steer", 350, 520, [("STEERANGLE", "deg", "0")]),
            _graph_block("Throttle", 520, 680, [("THROTTLE", "%", "0")]),
            _graph_block("Brake", 680, 840, [("BRAKE", "%", "0")]),
            _graph_block("Gear", 840, 1000, [("GEAR", "no", "0"), ("RPMS", "rpm", "0")], active="GEAR"),
        ],
        activate="Speed",
    )
    track_map = _track_map_worksheet()
    driver = _time_distance_worksheet(
        "Driver",
        [
            _graph_block("Speed", 0, 280, [("SPEED_KMH", "km/h", "1"), ("SPEED", "m/s", "2")], active="SPEED_KMH"),
            _graph_block("Steer", 280, 440, [("STEERANGLE", "deg", "0")]),
            _graph_block(
                "Pedals",
                440,
                640,
                [("BRAKE", "%", "0"), ("THROTTLE", "%", "0")],
                active="THROTTLE",
            ),
            _graph_block(
                "G",
                640,
                820,
                [("G_LAT", "G", "2"), ("G_LON", "G", "2")],
                active="G_LON",
            ),
            _graph_block(
                "CoastGap",
                820,
                1000,
                [("VRE_COAST", "no", "0"), ("VRE_PEDAL_GAP", "no", "0")],
                active="VRE_COAST",
            ),
        ],
        activate="Speed",
    )
    usos = _time_distance_worksheet(
        "Understeer Oversteer",
        [
            _graph_block("USOS", 0, 400, [("VRE_US_OS", "no", "2")]),
            _graph_block("Steer", 400, 600, [("STEERANGLE", "deg", "0")]),
            _graph_block("Glat", 600, 800, [("G_LAT", "G", "2")]),
            _graph_block("Speed", 800, 1000, [("SPEED_KMH", "km/h", "1")]),
        ],
        activate="USOS",
    )
    electronics = _time_distance_worksheet(
        "Electronics",
        [
            _graph_block("Speed", 0, 300, [("SPEED_KMH", "km/h", "1")]),
            _graph_block(
                "SpinLock",
                300,
                500,
                [("VRE_SPIN", "no", "0"), ("VRE_LOCK", "no", "0")],
                active="VRE_SPIN",
            ),
            _graph_block(
                "TCABS",
                500,
                750,
                [("VRE_TC_ACT", "no", "0"), ("VRE_ABS_ACT", "no", "0")],
                active="VRE_TC_ACT",
            ),
            _graph_block(
                "Levels",
                750,
                1000,
                [("VRE_TC_LEVEL", "no", "0"), ("VRE_ABS_LEVEL", "no", "0")],
                active="VRE_TC_LEVEL",
            ),
        ],
        activate="Speed",
    )
    sheets = "\n".join([track_map, compare, driver, usos, electronics])
    return (
        '<?xml version="1.0"?>\n'
        '<Workbook Locale="English_United States.1252" DefaultLocale="C" '
        'Version="200" Name="VRE Compare" Profile="Circuit" Active="Worksheets">\n'
        ' <Worksheets Version="100" Name="Worksheets" PosPC="0,0,1000,1000" Active="Track Map">\n'
        f"{sheets}\n"
        " </Worksheets>\n"
        "</Workbook>\n"
    )


def build_overlay_workbook_xml(labels: Sequence[str]) -> str:
    """Workbook for overlay.ld with Label_CHANNEL prefixes."""
    if len(labels) < 1:
        labels = ["Driver"]
    speed_traces = [(f"{lab}_SPEED_KMH", "km/h", "1") for lab in labels]
    thr_traces = [(f"{lab}_THROTTLE", "%", "0") for lab in labels]
    brk_traces = [(f"{lab}_BRAKE", "%", "0") for lab in labels]
    steer_traces = [(f"{lab}_STEERANGLE", "deg", "0") for lab in labels]
    gear_traces = [(f"{lab}_GEAR", "no", "0") for lab in labels]

    compare = _time_distance_worksheet(
        "Overlay Compare",
        [
            _graph_block("Speed", 0, 350, speed_traces),
            _graph_block("Steer", 350, 520, steer_traces),
            _graph_block("Throttle", 520, 680, thr_traces),
            _graph_block("Brake", 680, 840, brk_traces),
            _graph_block("Gear", 840, 1000, gear_traces),
        ],
        activate="Speed",
    )
    return (
        '<?xml version="1.0"?>\n'
        '<Workbook Locale="English_United States.1252" DefaultLocale="C" '
        'Version="200" Name="VRE Overlay" Profile="Circuit" Active="Worksheets">\n'
        ' <Worksheets Version="100" Name="Worksheets" PosPC="0,0,1000,1000" '
        'Active="Overlay Compare">\n'
        f"{compare}\n"
        " </Worksheets>\n"
        "</Workbook>\n"
    )


def _outing_range_id(ld_path: Path) -> str:
    """Full Motec Range.Id for Outings (path::::time, comment )."""
    win = to_motec_windows_path(ld_path)
    try:
        from vre_rpy.rpy_motec_ld import motec_outing_key

        key = motec_outing_key(ld_path)
    except Exception:
        key = None
    if key:
        return f"{win}::::{key}"
    return win


def _lap_range_id(ld_path: Path) -> str:
    """Laps Range.Id — prefer Fastest Lap from multi-beacon .ldx (not mega-lap)."""
    try:
        from vre_rpy.rpy_motec_ld import ldx_fastest_lap_label

        lap = ldx_fastest_lap_label(ld_path)
    except Exception:
        lap = "Lap 1"
    return f"{to_motec_windows_path(ld_path)}::::{lap}"


def install_acc_track_maps(dest_track_maps: Path) -> List[str]:
    """Copy ACC circuit .mt2 maps into a workspace Track Maps folder.

    Sources (first hit wins per filename): ACC_NN / ACC Workspace V1 under
    Documents\\MoTeC\\i2\\Workspaces, plus any path in MOTEC_ACC_TRACK_MAPS.

    When a track config with named corners exists, section ``Turn N`` labels in
    the .mt2 are rewritten (La Source, Eau Rouge, …) so ShowSectLabels is useful.
    """
    dest_track_maps = Path(dest_track_maps)
    dest_track_maps.mkdir(parents=True, exist_ok=True)

    vehicle_hints = (
        "GT3",
        "Vehicle",
        "Continental",
        "GTR",
        "Huracan",
        "M6",
        "R8 LMS",
        "RC F",
        "V12",
        "488",
        "991",
        "AMG",
    )
    docs = Path("/mnt/c/Users/Pranav/OneDrive/Documents/MoTeC/i2/Workspaces")
    if not docs.is_dir():
        # Native Windows / alternate user
        home = Path.home()
        for cand in (
            home / "OneDrive/Documents/MoTeC/i2/Workspaces",
            home / "Documents/MoTeC/i2/Workspaces",
        ):
            if cand.is_dir():
                docs = cand
                break

    sources: List[Path] = []
    env = os.environ.get("MOTEC_ACC_TRACK_MAPS")
    if env:
        sources.append(Path(env))
    pack_maps = Path(__file__).resolve().parent / "track_pack" / "mt2"
    if pack_maps.is_dir():
        sources.append(pack_maps)
    for name in ("ACC_NN", "ACC Workspace V1", "ACC_NN_BASE", "PRANAV_EDIT"):
        sources.append(docs / name / "Track Maps")

    try:
        from vre_rpy.mt2_sections import (
            load_track_config,
            patch_mt2_from_track_config,
            track_id_for_mt2,
        )
    except Exception:  # pragma: no cover
        load_track_config = None  # type: ignore
        patch_mt2_from_track_config = None  # type: ignore
        track_id_for_mt2 = None  # type: ignore

    copied: List[str] = []
    seen: set[str] = set()
    for src_dir in sources:
        if not src_dir.is_dir():
            continue
        for p in sorted(src_dir.glob("*.mt2")):
            if any(h in p.name for h in vehicle_hints):
                continue
            if p.name in seen:
                continue
            dest = dest_track_maps / p.name
            dest.write_bytes(p.read_bytes())
            if track_id_for_mt2 and patch_mt2_from_track_config and load_track_config:
                tid = track_id_for_mt2(p.name)
                if tid:
                    cfg = load_track_config(tid)
                    if cfg.get("corners"):
                        try:
                            patch_mt2_from_track_config(dest, cfg, out_path=dest)
                        except Exception:
                            pass
            seen.add(p.name)
            copied.append(p.name)
    return copied


def _build_workspace_i2wsp(
    *,
    motec_dir: Path,
    drivers: Sequence[Dict[str, Any]],
    overlay_ld: Optional[Path],
    include_overlay_workbook: bool,
) -> str:
    explore = to_motec_windows_path(motec_dir)
    sources: List[str] = []
    for d in drivers:
        ld = Path(d["ld"])
        sources.append(
            f'   <LoggedDataSource Name="{_xml_escape(to_motec_windows_path(ld))}"/>'
        )
    main = drivers[0]
    main_ld = Path(main["ld"])
    main_lap = _lap_range_id(main_ld)
    main_outing = _outing_range_id(main_ld)
    ref_block = ""
    outing_ref = ""
    if len(drivers) >= 2:
        ref_ld = Path(drivers[1]["ld"])
        ref_block = f'\n   <Ref Range.Id="{_xml_escape(_lap_range_id(ref_ld))}"/>'
        outing_ref = f'\n   <Ref Range.Id="{_xml_escape(_outing_range_id(ref_ld))}"/>'

    # Compare workbook (Track Map + ACC channels) is default; Overlay workbook
    # is available when overlay.ld exists (prefixed Tomato_*/Erik_* traces).
    wb_lines = [
        ' <Workbooks Version="100" Name="My Workbooks" Active="VRE Compare">',
        r'  <Workbook XRef="Workbooks\VRE_Compare.i2wkb" Name="VRE Compare"/>',
    ]
    if include_overlay_workbook:
        wb_lines.append(
            r'  <Workbook XRef="Workbooks\VRE_Overlay.i2wkb" Name="VRE Overlay"/>'
        )
    wb_lines.append(" </Workbooks>")

    # ExcludeUntrusted=0 is required: Motec strips rpy-built .ld DataSources when 1.
    return f"""<?xml version="1.0"?>
<Analysis Locale="English_United States.1252" DefaultLocale="C" Version="103" ShowOverlays="1" ExcludeUntrusted="0" IncludeOverlays="0" GraphTimeFormat="4" RemoveOnChanDblClick="0" ProfileId="Circuit" SetupSheetPasswdReqd="0" LapTimeDisplayFormat="0" LapTimeDisplayUnit="km/h" LapNameInOut="0" AutoLapNumber="0" LapTrustPC="0" Splitter.PC="7500">
 <Resources/>
 <LatGTrackGen LateralGChannel="G_LAT"/>
 <DataSelect SelectFastestOnLoad="0" DefaultShowDisabled="0" MainColorSource="0">
  <Explore HRef="{_xml_escape(explore)}"/>
  <DataSources TelemetryEnabled="1">
{chr(10).join(sources)}
  </DataSources>
  <View Name="Laps" Color="128,240,0" View="Laps">
   <Main Range.Id="{_xml_escape(main_lap)}"/>{ref_block}
  </View>
  <View Name="Outings" Color="240,128,0" View="Outings">
   <Main Range.Id="{_xml_escape(main_outing)}"/>{outing_ref}
  </View>
 </DataSelect>
 <ChannelsWindow/>
 <CAN/>
 <ValuesWindow SplitterPos="7739" ViewMode="1"/>
 <QuickSelectKeys>
  <Key Name="F5" Customised="0" Prompted="0"/>
  <Key Name="F6" Customised="0" Prompted="0"/>
  <Key Name="F7" Customised="0" Prompted="0"/>
  <Key Name="F8" Customised="0" Prompted="0"/>
 </QuickSelectKeys>
{chr(10).join(wb_lines)}
 <DockWindows>
  <DockWindow Id="Data" Visible="1" IsDocked="1" IsPinned="1" DockOffset="0" DockSide="35" DockIndex="0" Height="110" Width="220"/>
  <DockWindow Id="Channels" Visible="1" IsDocked="1" IsPinned="1" DockOffset="0" DockSide="35" DockIndex="2" Height="110" Width="220"/>
  <DockWindow Id="Reports" Visible="1" IsDocked="1" IsPinned="1" DockOffset="0" DockSide="35" DockIndex="1" Height="110" Width="220"/>
  <DockWindow Id="Layouts" Visible="1" IsDocked="1" IsPinned="1" DockOffset="0" DockSide="35" DockIndex="3" Height="110" Width="220"/>
  <DockWindow Id="Maths" Visible="1" IsDocked="1" IsPinned="1" DockOffset="0" DockSide="35" DockIndex="4" Height="110" Width="220"/>
 </DockWindows>
 <TelemetrySettings/>
</Analysis>
"""


def _launcher_ps1(
    *,
    workspace_name: str,
    driver_ld_names: Sequence[str],
    overlay_ld_name: Optional[str] = None,
) -> str:
    """ASCII-only PowerShell 5.1-safe launcher.

    MoTeC i2 on this machine: ``-w`` alone opens the workspace shell but leaves
    graphs empty (DataSources in i2wsp are not auto-activated). ``-w`` + one
    ``-l <main.ld>`` loads Main; a second ``-l`` exits code 1, so Overlay/Ref
    comes from rewritten DataSources once Main is open. Copy .ld files into
    workspace Logs\\, rewrite DataSources/Laps/Outings to those local paths,
    then launch ``-w <workspace> -l <main.ld>``.

    Splash ("Choose a Workspace") is a WebView2 page that ignores ``-w`` unless
    registry ``LoadPrevWorkspace`` (= Options Startup.Workspace.OpenMRU) is 1.
    Set that + pin RecentFiles\\0 to our workspace before starting i2.
    """
    names_ps = ",\n  ".join(f'    "{n}"' for n in driver_ld_names)
    overlay_ps = f'"{overlay_ld_name}"' if overlay_ld_name else "$null"
    return f"""# Auto-generated by export_rpy_analysis - one-click Motec open (ASCII only)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
if (-not $Root) {{ $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }}

# UNC (\\\\wsl$\\...) breaks CMD cwd; re-exec from a local Documents copy.
if ($Root -match '^\\\\\\\\') {{
  $DocsEarly = [Environment]::GetFolderPath('MyDocuments')
  $LocalLaunch = Join-Path $DocsEarly 'MoTeC\\VRE_Launcher_{workspace_name}'
  Write-Host "UNC source detected; installing local launcher -> $LocalLaunch"
  New-Item -ItemType Directory -Force -Path $LocalLaunch | Out-Null
  $rcCopy = (Start-Process -FilePath 'robocopy.exe' -ArgumentList @(
    $Root, $LocalLaunch, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/nc', '/ns', '/np'
  ) -Wait -PassThru).ExitCode
  if ($rcCopy -ge 8) {{
    Write-Host "ERROR: robocopy to local launcher failed code $rcCopy" -ForegroundColor Red
    Write-Host 'Press Enter to close...'
    [void][System.Console]::ReadLine()
    exit 1
  }}
  $localPs1 = Join-Path $LocalLaunch 'Open_VRE.ps1'
  Write-Host "Re-launching from $localPs1"
  $re = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $localPs1
  ) -Wait -PassThru
  exit $re.ExitCode
}}

$Docs = [Environment]::GetFolderPath('MyDocuments')
$DestWorkspace = Join-Path $Docs 'MoTeC\\i2\\Workspaces\\{workspace_name}'
$DestLogs = Join-Path $DestWorkspace 'Logs'
$SrcWorkspace = Join-Path $Root '{workspace_name}'
$I2Candidates = @(
  'C:\\Program Files\\MoTeC\\i2\\1.1\\i2.exe',
  'C:\\Program Files (x86)\\MoTeC\\i2\\1.1\\i2.exe'
)
$I2 = $I2Candidates | Where-Object {{ Test-Path $_ }} | Select-Object -First 1
$LogNames = @(
{names_ps}
)
$OverlayName = {overlay_ps}

function Fail([string]$Msg) {{
  Write-Host ''
  Write-Host "ERROR: $Msg" -ForegroundColor Red
  Write-Host 'Press Enter to close...'
  [void][System.Console]::ReadLine()
  exit 1
}}

if (-not $I2) {{ Fail 'MoTeC i2.exe not found under Program Files\\MoTeC\\i2' }}
if (-not (Test-Path $SrcWorkspace)) {{ Fail "Workspace folder missing: $SrcWorkspace" }}

New-Item -ItemType Directory -Force -Path $DestWorkspace | Out-Null
New-Item -ItemType Directory -Force -Path $DestLogs | Out-Null

Write-Host "Syncing workspace -> $DestWorkspace"
$rc = (Start-Process -FilePath 'robocopy.exe' -ArgumentList @(
  $SrcWorkspace, $DestWorkspace, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/nc', '/ns', '/np'
) -Wait -PassThru).ExitCode
if ($rc -ge 8) {{ Fail "robocopy workspace failed code $rc" }}

$LocalLogs = @()
foreach ($name in $LogNames) {{
  $src = Join-Path $Root $name
  if (-not (Test-Path $src)) {{ Fail "Log missing: $src" }}
  $dst = Join-Path $DestLogs $name
  Copy-Item -Force $src $dst
  # companion .ldx (multi-lap beacons) + outing / fastest-lap sidecars
  $srcx = $src + 'x'
  if (Test-Path $srcx) {{ Copy-Item -Force $srcx ($dst + 'x') }}
  foreach ($suf in @('.vre_outing', '.vre_beacons', '.vre_fastest_lap', '.vre_fastest_time')) {{
    $srcSide = $src + $suf
    if (Test-Path $srcSide) {{ Copy-Item -Force $srcSide ($dst + $suf) }}
  }}
  $LocalLogs += $dst
  Write-Host "  copied $name"
}}
$overlayLd = $null
if ($OverlayName) {{
  $srcO = Join-Path $Root $OverlayName
  if (Test-Path $srcO) {{
    $overlayLd = Join-Path $DestLogs $OverlayName
    Copy-Item -Force $srcO $overlayLd
    $srcOx = $srcO + 'x'
    if (Test-Path $srcOx) {{ Copy-Item -Force $srcOx ($overlayLd + 'x') }}
    Write-Host ("  copied overlay " + $OverlayName)
  }}
}}

# Rewrite Workspace.i2wsp so Motec auto-loads local Logs (no -l flag)
$Wsp = Join-Path $DestWorkspace 'Workspace.i2wsp'
if (-not (Test-Path $Wsp)) {{ Fail "Workspace.i2wsp missing after sync: $Wsp" }}
$xml = [System.IO.File]::ReadAllText($Wsp)
# CRITICAL: Motec strips rpy .ld DataSources when ExcludeUntrusted=1
$xml = $xml.Replace('ExcludeUntrusted="1"', 'ExcludeUntrusted="0"')
# Keep our Main/Ref flying-lap picks; Motec's SelectFastestOnLoad picks invalid laps
$xml = $xml.Replace('SelectFastestOnLoad="1"', 'SelectFastestOnLoad="0"')
$xml = [regex]::Replace($xml, 'Explore HRef="[^"]*"', ('Explore HRef="' + $DestLogs + '"'))

function Outing-Key([string]$LdPath) {{
  $meta = $LdPath + '.vre_outing'
  if (Test-Path $meta) {{
    return ([System.IO.File]::ReadAllText($meta)).TrimEnd([char]13, [char]10)
  }}
  return $null
}}

function Fastest-Lap([string]$LdPath) {{
  $side = $LdPath + '.vre_fastest_lap'
  if (Test-Path $side) {{
    $v = ([System.IO.File]::ReadAllText($side)).Trim()
    if ($v -match '^Lap\\s+\\d+') {{ return $v }}
    if ($v -match '^\\d+$') {{ return ('Lap ' + $v) }}
  }}
  $ldx = $LdPath + 'x'
  if (Test-Path $ldx) {{
    $m = [regex]::Match([System.IO.File]::ReadAllText($ldx), 'Id="Fastest Lap" Value="(\\d+)"')
    if ($m.Success) {{ return ('Lap ' + $m.Groups[1].Value) }}
  }}
  return 'Lap 1'
}}

$dsLines = New-Object System.Collections.Generic.List[string]
$dsLines.Add('  <DataSources TelemetryEnabled="1">')
foreach ($ld in $LocalLogs) {{
  $dsLines.Add(('   <LoggedDataSource Name="' + $ld + '"/>'))
}}
$dsLines.Add('  </DataSources>')
$mainLd = $LocalLogs[0]
$lapMain = Fastest-Lap $mainLd
$lapView = New-Object System.Collections.Generic.List[string]
$lapView.Add('  <View Name="Laps" Color="128,240,0" View="Laps">')
# Multi-beacon .ldx from arr24 S/F - select fastest flying lap, not mega-session
$lapView.Add(('   <Main Range.Id="' + $mainLd + '::::' + $lapMain + '"/>'))
if ($LocalLogs.Count -ge 2) {{
  $lapRef = Fastest-Lap $LocalLogs[1]
  $lapView.Add(('   <Ref Range.Id="' + $LocalLogs[1] + '::::' + $lapRef + '"/>'))
}}
$lapView.Add('  </View>')
$outView = New-Object System.Collections.Generic.List[string]
$outView.Add('  <View Name="Outings" Color="240,128,0" View="Outings">')
$ok0 = Outing-Key $mainLd
if ($ok0) {{ $outView.Add(('   <Main Range.Id="' + $mainLd + '::::' + $ok0 + '"/>')) }}
else {{ $outView.Add(('   <Main Range.Id="' + $mainLd + '"/>')) }}
if ($LocalLogs.Count -ge 2) {{
  $ok1 = Outing-Key $LocalLogs[1]
  if ($ok1) {{ $outView.Add(('   <Ref Range.Id="' + $LocalLogs[1] + '::::' + $ok1 + '"/>')) }}
  else {{ $outView.Add(('   <Ref Range.Id="' + $LocalLogs[1] + '"/>')) }}
}}
$outView.Add('  </View>')
$xml = [regex]::Replace($xml, '(?s)<DataSources[^>]*>.*?</DataSources>', ($dsLines -join "`r`n"))
$xml = [regex]::Replace($xml, '(?s)<View Name="Laps"[^>]*>.*?</View>', ($lapView -join "`r`n"))
$xml = [regex]::Replace($xml, '(?s)<View Name="Outings"[^>]*>.*?</View>', ($outView -join "`r`n"))
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Wsp, $xml, $utf8)
Write-Host ("Workspace DataSources -> local Logs (" + $lapMain + " / ExcludeUntrusted=0)")

# Skip WebView2 "Choose a Workspace" splash (Startup.Workspace.OpenMRU)
$MotecReg = 'HKCU:\\Software\\MoTeC\\i2\\1.1'
if (-not (Test-Path $MotecReg)) {{ New-Item -Path $MotecReg -Force | Out-Null }}
New-ItemProperty -Path $MotecReg -Name 'LoadPrevWorkspace' -PropertyType DWord -Value 1 -Force | Out-Null
$rfRoot = Join-Path $MotecReg 'RecentFiles'
$rf0 = Join-Path $rfRoot '0'
if (-not (Test-Path $rfRoot)) {{ New-Item -Path $rfRoot -Force | Out-Null }}
if (-not (Test-Path $rf0)) {{ New-Item -Path $rf0 -Force | Out-Null }}
New-ItemProperty -Path $rf0 -Name 'Name' -PropertyType String -Value '{workspace_name}' -Force | Out-Null
New-ItemProperty -Path $rf0 -Name 'Path' -PropertyType String -Value $DestWorkspace -Force | Out-Null
Write-Host 'Motec LoadPrevWorkspace=1; RecentFiles[0]={workspace_name}'

# Close a stuck splash from a previous launch so -w can take over
Get-Process i2 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 400

Write-Host "Opening Motec: {workspace_name} / VRE Compare"
Write-Host "  Workspace: $DestWorkspace"
Write-Host "  Hotkeys: F9=distance  F3=variance"
Write-Host "  (launch: -w workspace -l main.ld - required for auto data load)"

try {{
  # -w alone = empty graphs. -w + one -l activates Main from Logs.
  # Do not pass a second -l (i2 exits code 1).
  # Boot Main (unprefixed ACC channels) so Track Map / G_LAT / venue .mt2 work.
  # Then clipboard-open Ref (fastest valid) + optional overlay.ld for VRE Overlay.
  $bootLd = $mainLd
  $launchArgs = @('-w', $DestWorkspace, '-l', $bootLd)
  $p = Start-Process -FilePath $I2 -ArgumentList $launchArgs -PassThru -WorkingDirectory (Split-Path $I2)
  if (-not $p) {{ Fail 'Start-Process returned no process' }}
  Write-Host ("Motec PID " + $p.Id + " -l " + $bootLd)
  Start-Sleep -Seconds 5
  if ($p.HasExited) {{
    Fail ("i2.exe exited immediately code=" + $p.ExitCode)
  }}
  $title = (Get-Process -Id $p.Id -ErrorAction SilentlyContinue).MainWindowTitle
  Write-Host ("Motec window: " + $title)
  if ($title -notmatch [regex]::Escape('{workspace_name}')) {{
    Write-Host 'WARNING: splash may still be up; LoadPrevWorkspace should skip it on next run.' -ForegroundColor Yellow
  }}
  if ($LocalLogs.Count -ge 2) {{
    $refLd = $LocalLogs[1]
    $refLap = Fastest-Lap $refLd
    Write-Host ''
    Write-Host 'COMPARE: Main is loaded. Ref (fastest VALID flying lap) is listed in DataSources:' -ForegroundColor Cyan
    Write-Host ("  Ref file: " + $refLd)
    Write-Host ("  Ref lap:  " + $refLap)
    Write-Host '  In MoTeC Data pane: select that Ref lap, press R (set Reference).' -ForegroundColor Cyan
    Write-Host '  Or open workbook VRE Overlay + Logs\\overlay.ld for Tomato_* vs Erik_* traces.'
    if ($overlayLd) {{
      Write-Host ("  overlay.ld ready: " + $overlayLd)
    }}
  }}
  Write-Host 'Motec is running. Track Map worksheet shows labeled corners. You can close this window.'
}} catch {{
  Fail ("Start-Process failed: " + $_.Exception.Message)
}}
"""


def _launcher_bat() -> str:
    # pushd maps UNC (\\wsl$\\...) to a drive letter so powershell -File works;
    # Open_VRE.ps1 then re-copies to Documents\\MoTeC\\VRE_Launcher_* and re-execs.
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "REM Double-click to open Motec (copies data to Documents, then launches i2)\r\n"
        "cd /d \"%~dp0\"\r\n"
        "pushd \"%~dp0\" 2>nul\r\n"
        "where powershell >nul 2>&1 || (\r\n"
        "  echo ERROR: powershell.exe not found\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File \"%~dp0Open_VRE.ps1\"\r\n"
        "set ERR=%ERRORLEVEL%\r\n"
        "popd 2>nul\r\n"
        "if not \"%ERR%\"==\"0\" (\r\n"
        "  echo.\r\n"
        "  echo Launcher failed code %ERR%\r\n"
        "  pause\r\n"
        ")\r\n"
    )


def write_motec_workspace_bundle(
    *,
    motec_dir: Path,
    drivers: Sequence[Dict[str, Any]],
    overlay_ld: Optional[Path] = None,
    workspace_name: str = "VRE_Export",
) -> Dict[str, Any]:
    """Write Motec workspace + workbooks + Open_VRE.bat/ps1 under motec_dir.

    ``drivers`` items: ``{"label": str, "ld": Path}`` — first = Main, second = Overlay.
    """
    if not drivers:
        raise ValueError("drivers required")
    motec_dir = Path(motec_dir)
    motec_dir.mkdir(parents=True, exist_ok=True)

    ws_dir = motec_dir / workspace_name
    wb_dir = ws_dir / "Workbooks"
    ch_dir = ws_dir / "Channels"
    math_dir = ws_dir / "Maths"
    track_dir = ws_dir / "Track Maps"
    for d in (wb_dir, ch_dir, math_dir, track_dir):
        d.mkdir(parents=True, exist_ok=True)

    labels = [str(d["label"]) for d in drivers]
    include_overlay_wb = overlay_ld is not None and len(labels) >= 1

    compare_xml = build_compare_workbook_xml()
    (wb_dir / "VRE_Compare.i2wkb").write_bytes(
        compare_xml.replace("\n", "\r\n").encode("utf-8")
    )
    if include_overlay_wb:
        overlay_xml = build_overlay_workbook_xml(labels)
        (wb_dir / "VRE_Overlay.i2wkb").write_bytes(
            overlay_xml.replace("\n", "\r\n").encode("utf-8")
        )

    (wb_dir / "Reports.xml").write_text(
        '<?xml version="1.0"?>\r\n'
        '<Reports Locale="English_United States.1252" DefaultLocale="C"/>\r\n',
        encoding="utf-8",
    )
    for name, body in (
        ("Aliases.xml", '<Aliases Locale="English_United States.1252" DefaultLocale="C"/>'),
        ("Mappings.xml", '<Items Locale="English_United States.1252" DefaultLocale="C"/>'),
        ("Details.xml", '<Details Locale="English_United States.1252" DefaultLocale="C"/>'),
        (
            "Overrides.xml",
            '<Overrides Locale="English_United States.1252" DefaultLocale="C"/>',
        ),
    ):
        (ch_dir / name).write_text(
            f'<?xml version="1.0"?>\r\n{body}\r\n', encoding="utf-8"
        )

    track_maps = install_acc_track_maps(track_dir)

    wsp = _build_workspace_i2wsp(
        motec_dir=motec_dir,
        drivers=drivers,
        overlay_ld=overlay_ld,
        include_overlay_workbook=include_overlay_wb,
    )
    (ws_dir / "Workspace.i2wsp").write_bytes(wsp.replace("\n", "\r\n").encode("utf-8"))

    (ws_dir / "README.md").write_text(
        "# VRE Motec workspace\n\n"
        "Generated by `export_rpy_analysis`.\n\n"
        "Double-click `../Open_VRE.bat` - copies into "
        f"`Documents\\\\MoTeC\\\\i2\\\\Workspaces\\\\{workspace_name}` and opens "
        "i2 with **VRE Compare** (Main + Overlay).\n\n"
        f"Track maps installed: {len(track_maps)} ACC circuits "
        "(Motec Track Maps / .mt2).\n\n"
        "Hotkeys: **F9** distance, **F3** variance.\n",
        encoding="utf-8",
    )

    # Ensure driver .ld basenames live beside the launcher (copy if needed)
    ld_names: List[str] = []
    try:
        from vre_rpy.rpy_motec_ld import (
            motec_outing_key,
            write_ldx_beacons,
        )
    except Exception:  # pragma: no cover
        motec_outing_key = None  # type: ignore
        write_ldx_beacons = None  # type: ignore

    for d in drivers:
        src_ld = Path(d["ld"])
        name = src_ld.name
        ld_names.append(name)
        dest_ld = motec_dir / name
        if src_ld.resolve() != dest_ld.resolve() and src_ld.is_file():
            dest_ld.write_bytes(src_ld.read_bytes())
            for suf in (
                "x",
                ".vre_outing",
                ".vre_beacons",
                ".vre_fastest_lap",
                ".vre_fastest_time",
            ):
                src_side = (
                    Path(str(src_ld) + "x") if suf == "x" else Path(str(src_ld) + suf)
                )
                if src_side.is_file():
                    dest_side = (
                        Path(str(dest_ld) + "x")
                        if suf == "x"
                        else Path(str(dest_ld) + suf)
                    )
                    dest_side.write_bytes(src_side.read_bytes())
        target = dest_ld if dest_ld.is_file() else src_ld
        if target.is_file() and write_ldx_beacons is not None:
            try:
                # Never smash a multi-lap .ldx from export with a mega-lap fallback.
                write_ldx_beacons(target, overwrite=False)
            except Exception:
                pass
            if motec_outing_key is not None:
                key = motec_outing_key(target)
                if key:
                    # Sidecar for Open_VRE.ps1 Outings Range.Id rewrite
                    Path(str(target) + ".vre_outing").write_text(key, encoding="ascii")

    overlay_name = Path(overlay_ld).name if overlay_ld else None
    ps1 = _launcher_ps1(
        workspace_name=workspace_name,
        driver_ld_names=ld_names,
        overlay_ld_name=overlay_name,
    )
    # UTF-8 BOM so Windows PowerShell 5.1 parses the file correctly
    (motec_dir / "Open_VRE.ps1").write_bytes(
        ("\ufeff" + ps1).replace("\n", "\r\n").encode("utf-8")
    )
    (motec_dir / "Open_VRE.bat").write_bytes(_launcher_bat().encode("ascii"))

    open_txt = (
        "ONE-CLICK: double-click Open_VRE.bat\n\n"
        "Prefer the Windows copy (avoids WSL UNC):\n"
        f"  Documents\\MoTeC\\VRE_Launcher_{workspace_name}\\Open_VRE.bat\n"
        "  (first UNC double-click installs that folder automatically)\n\n"
        f"Workspace: motec/{workspace_name}/\n"
        "Active workbook: VRE Compare (Track Map + ACC channels)\n"
        "With 2+ drivers: Ref = fastest *valid* flying lap; Main = other.\n"
        "Launcher opens Main then clipboard-opens Ref (press R).\n"
        f"Launcher syncs to Documents\\MoTeC\\i2\\Workspaces\\{workspace_name}\\ + Logs\\\n"
        "Sets Motec LoadPrevWorkspace=1 + ExcludeUntrusted=0 so data loads.\n"
        f"Track maps: {len(track_maps)} ACC .mt2 files in Track Maps\\\n"
        "Hotkeys: F9 = distance, F3 = variance\n"
        "If a console shows ERROR, read it - window stays open.\n"
    )
    (motec_dir / "OPEN_WORKSPACE.txt").write_text(open_txt, encoding="utf-8")

    return {
        "workspace_name": workspace_name,
        "workspace_dir": str(ws_dir),
        "workspace_i2wsp": str(ws_dir / "Workspace.i2wsp"),
        "active_workbook": "VRE Compare",
        "workbooks": ["VRE Compare"]
        + (["VRE Overlay"] if include_overlay_wb else []),
        "launcher_bat": str(motec_dir / "Open_VRE.bat"),
        "launcher_ps1": str(motec_dir / "Open_VRE.ps1"),
        "main_ld": str(drivers[0]["ld"]),
        "overlay_ld": str(drivers[1]["ld"]) if len(drivers) >= 2 else None,
        "merged_overlay_ld": str(overlay_ld) if overlay_ld else None,
        "channels": list(ACC_COMPARE_CHANNELS),
        "track_maps": track_maps,
        "install_hint": (
            f"Documents\\\\MoTeC\\\\i2\\\\Workspaces\\\\{workspace_name} "
            "(via Open_VRE.bat)"
        ),
    }
