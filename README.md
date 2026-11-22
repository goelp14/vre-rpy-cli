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

### Frame-by-frame lap data

```bash
vre-rpy frames \
  --rpy /path/to/my_lap.rpy:Me \
  --rpy /path/to/reference.rpy:Ref \
  --out-dir ./frames --laps all        # or: best | 3,7,12
```

Writes `frames/<Label>/lap_NN.json` (one frame per recorded car position, 14–30 Hz depending on
replay quality) and `frames/index.json`. Each frame has distance along the track's AI line, car
position and elevation, body yaw, slip angle, how far the wheels are from each track edge, and the
driver inputs (speed, throttle, brake, steering, gear, rpm, G incl. vertical) synced onto the frame.

- **Any car, any track.** Car and track come from ids stored inside the replay, not the filename.
- **Race replays:** every car in the session is in the file. The default is your car; pick another
  with `--car-index` (`index.json` lists drivers). Other cars have position and inputs but no
  per-wheel tyre data.
- **Sync is fitted, not assumed**, and every lap reports `sync.lap_r` / `sync.lap_sync_go`. Laps that
  fail the check keep positions but should not be used for input-level comparison.
- Distances and track edges need your ACC install's `Content/Cache/<track>/fastlane.ai`
  (found automatically on a default Steam install; else set `VRE_ACC_CACHE`).

### Corner-by-corner analysis

```bash
vre-rpy analyze --rpy me.rpy:Me --ref fast.rpy:Ref --out analysis.json
```

Distances are metres from the S/F line. For each corner window (from one braking point to the next)
and every valid lap of both drivers: braking onset, metres to full pressure, deceleration, any second
brake application, brake at turn-in, entry and minimum speed (and where), throttle pickup, full throttle
that stays, coasting, lifts, wheel straight, peak slip angle, inside/outside edge margins, wheelspin and
lock (your car), and what the car was doing on each crest and dip. Plus per-metric consistency and
correlation with section time, the time gap to the reference along the lap, the steepest loss windows,
a sanity-checked theoretical best, and car state estimated from the data (mass, weight split,
wheelbase, downforce and aero balance, load transfer, understeer gradient, axle slip angles).

### Setups

```bash
vre-rpy setup --rpy me.rpy:Me --ref fast.rpy:Ref --out setup.json
vre-rpy setup --rpy me.rpy --use-setup "…/Setups/<car>/<track>/My setup.json" --calibration cal.json --out setup.json
```

A replay does **not** store the car setup (the loader accounts for every byte of the file). So this
command reports what can be known:

- **Exact, from the replay:** gearing (km/h per 1000 rpm per gear), tyre radii, static mass and weight
  split, car dimensions.
- **Estimated behaviour** with lap-to-lap spread: downforce and aero balance, braking slip split
  (brake-bias proxy), roll-stiffness split, pitch under braking, rake change with speed, diff
  behaviour, TC/ABS estimate rates. With `--ref`, a plain comparison that says "can't tell" when the
  difference is inside the noise.
- **Your setup library:** setups for that car/track saved before the session (unconfirmed until you
  pass `--use-setup`). Setup diffs are in clicks with what each change does; physical units are only
  shown where the file stores them (camber in degrees, fuel) or a convention is labelled as such.
- **Calibration:** each confirmed session teaches, per car, how a click moves each measured item.
  Nothing is ever written out as a loadable setup for someone else's replay.

### Track limits

```bash
vre-rpy limits --rpy a.rpy --rpy b.rpy --rpy c.rpy --out limits.json --zone-model spa_zones.json
```

For every lap: whether the game invalidated it, **where the cut happened**, which side, and how long
the game took to judge it. Every time wheels went past the edge is listed, labelled *judged* or
*over the line — not judged* (never "illegal"). Also writes the widest track use seen on still-valid
laps, per 10 m.

How it works: a replay stores the game's verdict (the lap-valid flag drops at the first cut of a lap).
ACC often judges cuts later, at fixed places per track, so the place a flag drops is not always where
the cut was. `--zone-model` learns, per track, which excursion each judging point belongs to; give it
more replays of the same track to make attribution more reliable. Edges are the AI line's official
widths, which match the painted lines in many but not all places.

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

## Track packs

`vre_rpy/track_pack/generated/` ships a pack for every ACC track, generated from the game's AI line:
corners (position, direction, tightest radius, speed class, complexes) and terrain features (crests and
dips with the load change they cause, `dg_at_100kmh`, which scales with speed squared). Regenerate from
your own install with `vre-rpy track-pack --out-dir packs/`.

Optional `track_pack/notes/<track>.json` files add researched corner names, cues and known
track-limit hotspots (each with sources). Notes only relabel and annotate; they never change detection,
so every track works the same with or without them.

## Track configs

Named corners for ACC circuits live in `src/vre_rpy/track_pack/*.json`. Add or edit a JSON to improve labels for a venue — the next export patches that track’s `.mt2` section names automatically (same-length renames only).

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
```

Tests that need real replays skip unless `VRE_RPY_FIXTURES` points at local folders
(`os.pathsep`-separated) holding them. Replays are never committed.

## License

MIT
