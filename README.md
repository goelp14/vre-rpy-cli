# vre-rpy

CLI that turns **Assetto Corsa Competizione** `.rpy` replays into:

1. **AI-labeled lap data** — per-lap times, validity, and coaching-ready JSON
2. **MoTeC i2 Pro workspace** — `.ld` / `.ldx` logs, multi-lap beacons, track map with named corners, and a one-click Windows launcher

## Requirements

- Python 3.10+
- Windows with [MoTeC i2 Pro](https://www.motec.com.au/products/i2) (to open the generated workspace)
- Your own `.rpy` replay files from ACC

## Install

```bash
git clone https://github.com/<you>/vre-rpy-cli.git
cd vre-rpy-cli
python -m pip install -e .
```

## Quick start

```bash
vre-rpy \
  --rpy /path/to/my_lap.rpy:Tomato \
  --rpy /path/to/reference.rpy:Erik \
  --out-dir ./out_spa
```

Then on Windows, double-click:

```text
out_spa/motec/Open_VRE.bat
```

That copies the workspace into `Documents\MoTeC\i2\Workspaces\VRE_Export`, loads the data, and opens MoTeC.

### Single replay

```bash
vre-rpy --rpy session.rpy:Me --out-dir ./out
```

### Electronics maps (optional)

If you know garage TC/ABS maps for the session:

```bash
echo '{"TC1":7,"TC2":5,"ABS":2,"EngineMap":1}' > elec.json
vre-rpy --rpy session.rpy:Me --electronics elec.json --out-dir ./out
```

## What you get

| Path | Purpose |
|------|---------|
| `agent_pack/` | Per-driver JSON streams for AI / coaching tools |
| `ai_series/*_laps.json` | Motec-aligned lap labels + validity |
| `motec/<Label>.ld` + `.ldx` | MoTeC log + lap beacons |
| `motec/overlay.ld` | Both drivers on prefixed channel names |
| `motec/VRE_Export/` | i2 workspace (workbooks, track maps) |
| `motec/Open_VRE.bat` | One-click MoTeC launch |
| `drivers.json` | Full export summary |

### Main vs Reference (2+ replays)

When you pass two or more `.rpy` files, the CLI picks:

- **Ref** — driver with the **fastest valid flying lap**
- **Main** — the other driver (first remaining in your input order)

Invalid laps (track-limit cuts, etc.) are never used for that choice. MoTeC boots **Main** (Track Map + ACC channels). In the Data pane, select the Ref lap and press **R**. Workbook **VRE Overlay** + `overlay.ld` shows both drivers as `Tomato_*` / `Erik_*` traces.

### Track map + corner names

The workspace includes ACC circuit `.mt2` track maps (copied from your local MoTeC ACC workspaces when present, or set `MOTEC_ACC_TRACK_MAPS`).

Corner sections are relabeled with real names (e.g. Spa: La Source, Eau Rouge, Raidillon, …) from bundled track configs under `vre_rpy/track_pack/`. The **Track Map** worksheet shows the map with section labels as you scrub the data.

### Analytics channels

Beyond raw inputs (throttle, brake, steer, speed, G, wheels, temps, slip, …), the `.ld` includes derived channels such as:

- `VRE_US_OS`, `VRE_COAST`, `VRE_PEDAL_GAP`
- `VRE_SPIN` / `VRE_LOCK`, `VRE_TC_ACT` / `VRE_ABS_ACT`
- `VRE_COMBINED_G`, `VRE_TRAIL_BRAKE`, `VRE_BRAKE_RATE`, `VRE_THROTTLE_RATE`
- `VRE_DIST_M`, load / slip aggregates, brake-temp balance

## How the CLI works

```text
.rpy file(s)
    │
    ├─► decode inputs + pose + lap timer
    ├─► label laps (time, validity, Motec beacon alignment)
    ├─► write MoTeC .ld/.ldx (venue + multi-lap beacons)
    ├─► write AI JSON packs
    └─► build i2 workspace + Open_VRE.bat
```

1. **Parse** each `.rpy` into time-series channels and lap-timer rows.
2. **Validate laps** — completed flying laps marked valid/invalid from the replay’s lap-timer flags.
3. **Align Motec laps** — S/F crossings become `.ldx` beacons so MoTeC shows real lap times (not one mega-lap).
4. **Order compare** — with 2+ files, Ref = fastest valid flyer.
5. **Workspace** — workbooks (Track Map, Compare, Driver, Electronics), track maps with named sections, launcher that syncs into Documents and starts i2 with `-w` + `-l`.

## MoTeC tips

- Hotkeys: **F9** distance axis, **F3** variance
- Open the **Track Map** worksheet to see the car on track with corner labels
- If only Main appears, the launcher also sends Ctrl+O / **R** for the Ref log; you can still select Ref manually in the Data pane and press **R**
- Track maps: install any ACC MoTeC workspace once, or point `MOTEC_ACC_TRACK_MAPS` at a folder of `.mt2` files

## Track configs

Named corners for ACC circuits live in `src/vre_rpy/track_pack/*.json`. Add or edit a JSON to improve labels for a venue — the next export patches that track’s `.mt2` section names automatically.

## License

MIT
