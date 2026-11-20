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
git clone https://github.com/goelp14/vre-rpy-cli.git
cd vre-rpy-cli
python -m pip install -e .
```

## Quick start

```bash
vre-rpy export \
  --rpy /path/to/my_lap.rpy:Me \
  --rpy /path/to/reference.rpy:Ref \
  --out-dir ./out_spa
```

Flat flags still work (`vre-rpy --rpy … --out-dir …`).

Then on Windows, double-click:

```text
out_spa/motec/Open_VRE.bat
```

That copies the workspace into `Documents\MoTeC\i2\Workspaces\VRE_Export`, sets Motec `LoadPrevData=1`, and starts i2 with `-w` so **Main and Ref both load**.

### Single replay

```bash
vre-rpy export --rpy session.rpy:Me --out-dir ./out
```

### Verify an export (lap times + track map health)

```bash
vre-rpy verify \
  --motec-dir ./out_spa/motec \
  --driver /path/to/my_lap.rpy:./out_spa/motec/Me.ld:Me \
  --driver /path/to/reference.rpy:./out_spa/motec/Ref.ld:Ref
```

Checks (fail loud):

1. Best valid flying lap from the `.rpy` matches `.ldx` beacon gap (≤ 50 ms)
2. `.ldx` Details Fastest Time matches that lap
3. Workspace `Internal Maths.xml` lists **SPEED** (needed for Corr Dist / Track Map stroke)
4. Venue `.mt2` present and structurally valid (or warning if Motec has no map for that venue)
5. `.ld` has SPEED + G_LAT samples

### Electronics maps (optional)

```bash
echo '{"TC1":7,"TC2":5,"ABS":2,"EngineMap":1}' > elec.json
vre-rpy export --rpy session.rpy:Me --electronics elec.json --out-dir ./out
```

## What you get

| Path | Purpose |
|------|---------|
| `agent_pack/` | Per-driver JSON streams for AI / coaching tools |
| `ai_series/*_laps.json` | Motec-aligned lap labels + validity |
| `motec/<Label>.ld` + `.ldx` | MoTeC log + lap beacons |
| `motec/overlay.ld` | Both drivers on prefixed channel names |
| `motec/VRE_Export/` | i2 workspace (workbooks, track maps, Internal Maths) |
| `motec/Open_VRE.bat` | One-click MoTeC launch |
| `drivers.json` | Full export summary |

### Main vs Reference (2+ replays)

When you pass two or more `.rpy` files, the CLI picks:

- **Ref** — driver with the **fastest valid flying lap**
- **Main** — the other driver (first remaining in your input order)

Invalid laps (track-limit cuts, etc.) are never used for that choice. The launcher opens the workspace with **both** DataSources (Main + Ref) already loaded — no manual Ctrl+O.

### Track map + corner names

The workspace includes ACC circuit `.mt2` track maps (copied from your local MoTeC ACC workspaces when present, or set `MOTEC_ACC_TRACK_MAPS`).

Corner sections are relabeled with real names (e.g. Spa: La Source, Eau Rouge, Raidillon, …) from bundled track configs under `vre_rpy/track_pack/`. The **Track Map** worksheet shows the map with section labels as you scrub the data.

Corr Dist requires SPEED in CircuitMaths — the shipped `Internal Maths.xml` includes it. Track-map speed colour bands are km/h (GT3 range).

### Sample rate

Channels are written on a uniform grid (default **50 Hz**, override with env `VRE_MOTEC_HZ`) so Motec’s integer frequency field stays aligned with beacons. Gear/flag/map stay step-held; other channels are linearly interpolated for display.

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
3. **Align Motec laps** — S/F crossings become `.ldx` beacons so MoTeC shows real lap times (not one mega-lap). Beacons are never written at t=0 (Motec Lap 1 = log start → first BCN).
4. **Order compare** — with 2+ files, Ref = fastest valid flyer.
5. **Workspace** — workbooks (Track Map, Compare, Driver, Electronics), Internal Maths with SPEED, track maps with named sections, launcher with `LoadPrevData=1` and `i2 -w` only.

## MoTeC tips

- Hotkeys: **F9** distance axis, **F3** variance
- Open the **Track Map** worksheet to see the car on track with corner labels
- If the track outline is missing (labels only), re-export so `Internal Maths.xml` ships with SPEED, or run `vre-rpy verify`
- Venue name on the `.ld` must match the `.mt2` basename (case-insensitive)
- Track maps: install any ACC MoTeC workspace once, or point `MOTEC_ACC_TRACK_MAPS` at a folder of `.mt2` files

## Track configs

Named corners for ACC circuits live in `src/vre_rpy/track_pack/*.json`. Add or edit a JSON to improve labels for a venue — the next export patches that track’s `.mt2` section names automatically (same-length renames only).

## License

MIT
