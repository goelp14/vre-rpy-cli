"""ACC setup files (``Documents/Assetto Corsa Competizione/Setups/<car>/<track>/*.json``) — any car.

Setup files store click *indices* (plus a few physical floats: static camber in degrees, linear toe,
rod length). Index-to-unit tables are per car and live in the game's encrypted data, so this module
reports clicks, the physical floats that are stored, and — only where a convention is widely used —
an approximate unit, always labelled. ``FIELD_INFO`` says what increasing each setting does; that
ordering is the same for every ACC car (higher index = stiffer / more / higher).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

WHEELS = ("LF", "RF", "LR", "RR")

FIELD_INFO: Dict[str, Dict[str, str]] = {
    "basicSetup.tyres.tyrePressure": {"label": "Tyre pressure (cold)", "group": "tyres",
        "increase": "higher pressure: smaller contact patch, faster warm-up, less sidewall flex",
        "unit_note": "≈ 20.3 + 0.1 × click psi on GT3 (community convention, unverified per car)"},
    "basicSetup.alignment.camber": {"label": "Camber (clicks)", "group": "alignment",
        "increase": "less negative camber", "unit_note": "see staticCamber for the stored degrees"},
    "basicSetup.alignment.staticCamber": {"label": "Static camber", "group": "alignment",
        "increase": "less negative camber (deg, stored exactly)", "unit": "deg"},
    "basicSetup.alignment.toe": {"label": "Toe (clicks)", "group": "alignment",
        "increase": "more toe-out (front) / less toe-in (rear)"},
    "basicSetup.alignment.toeOutLinear": {"label": "Toe (linear, stored)", "group": "alignment",
        "increase": "more toe-out", "unit": "m"},
    "basicSetup.alignment.casterLF": {"label": "Caster LF", "group": "alignment",
        "increase": "more caster: more self-aligning torque and dynamic camber with lock"},
    "basicSetup.alignment.casterRF": {"label": "Caster RF", "group": "alignment",
        "increase": "more caster: more self-aligning torque and dynamic camber with lock"},
    "basicSetup.alignment.steerRatio": {"label": "Steering ratio", "group": "alignment",
        "increase": "changes steering ratio (car-specific direction)"},
    "basicSetup.electronics.tC1": {"label": "TC1", "group": "electronics", "increase": "more traction-control intervention"},
    "basicSetup.electronics.tC2": {"label": "TC2", "group": "electronics", "increase": "more TC cut (car-specific)"},
    "basicSetup.electronics.abs": {"label": "ABS", "group": "electronics", "increase": "more ABS intervention"},
    "basicSetup.electronics.eCUMap": {"label": "ECU map", "group": "electronics", "increase": "different engine map (car-specific)"},
    "basicSetup.strategy.fuel": {"label": "Fuel", "group": "strategy", "increase": "more fuel (litres)", "unit": "L"},
    "advancedSetup.mechanicalBalance.aRBFront": {"label": "Front anti-roll bar", "group": "mechanical",
        "increase": "stiffer front bar: more front load transfer → usually more understeer mid-corner, sharper response"},
    "advancedSetup.mechanicalBalance.aRBRear": {"label": "Rear anti-roll bar", "group": "mechanical",
        "increase": "stiffer rear bar: more rear load transfer → usually more rotation / oversteer"},
    "advancedSetup.mechanicalBalance.wheelRate": {"label": "Spring (wheel rate)", "group": "mechanical",
        "increase": "stiffer spring: less travel and pitch/roll, more load transfer on that axle, platform for aero"},
    "advancedSetup.mechanicalBalance.bumpStopRateUp": {"label": "Bump stop rate (bump)", "group": "mechanical",
        "increase": "stiffer bump stop once engaged"},
    "advancedSetup.mechanicalBalance.bumpStopRateDn": {"label": "Bump stop rate (rebound)", "group": "mechanical",
        "increase": "stiffer rebound bump stop once engaged"},
    "advancedSetup.mechanicalBalance.bumpStopWindow": {"label": "Bump stop range", "group": "mechanical",
        "increase": "more travel before the bump stop engages"},
    "advancedSetup.mechanicalBalance.brakeTorque": {"label": "Brake power", "group": "brakes",
        "increase": "more brake torque per pedal %", "unit_note": "percent (index = %)"},
    "advancedSetup.mechanicalBalance.brakeBias": {"label": "Brake bias", "group": "brakes",
        "increase": "more front bias (fronts lock first; more stable, less rotation on entry)",
        "unit_note": "% front = car-specific offset + 0.2 × click"},
    "advancedSetup.dampers.bumpSlow": {"label": "Slow bump", "group": "dampers",
        "increase": "slows weight transfer onto that corner (entry/turn-in response)"},
    "advancedSetup.dampers.bumpFast": {"label": "Fast bump", "group": "dampers",
        "increase": "firmer over kerbs and bumps"},
    "advancedSetup.dampers.reboundSlow": {"label": "Slow rebound", "group": "dampers",
        "increase": "slows weight leaving that corner (holds load longer)"},
    "advancedSetup.dampers.reboundFast": {"label": "Fast rebound", "group": "dampers",
        "increase": "slower recovery after kerbs/bumps"},
    "advancedSetup.aeroBalance.rideHeight": {"label": "Ride height (clicks)", "group": "aero",
        "increase": "higher ride height (array order is car-specific; rake = rear − front)"},
    "advancedSetup.aeroBalance.splitter": {"label": "Splitter", "group": "aero", "increase": "more front downforce"},
    "advancedSetup.aeroBalance.rearWing": {"label": "Rear wing", "group": "aero",
        "increase": "more rear downforce and drag (aero balance moves rearward)"},
    "advancedSetup.aeroBalance.brakeDuct": {"label": "Brake ducts", "group": "brakes",
        "increase": "more brake cooling (colder discs), small drag cost"},
    "advancedSetup.drivetrain.preload": {"label": "Diff preload", "group": "drivetrain",
        "increase": "more locking at low torque: more stable off-throttle, can push mid-corner"},
}


def default_setups_dir() -> Path:
    env = os.environ.get("VRE_ACC_SETUPS")
    if env:
        return Path(env)
    for c in (
        Path.home() / "Documents" / "Assetto Corsa Competizione" / "Setups",
        *Path("/mnt/c/Users").glob("*/OneDrive/Documents/Assetto Corsa Competizione/Setups"),
        *Path("/mnt/c/Users").glob("*/Documents/Assetto Corsa Competizione/Setups"),
    ):
        if Path(c).is_dir():
            return Path(c)
    return Path.home() / "Documents" / "Assetto Corsa Competizione" / "Setups"


def _flat(x: Any, p: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(x, dict):
        for k, v in x.items():
            out.update(_flat(v, f"{p}.{k}" if p else k))
    elif isinstance(x, list) and x and not all(isinstance(v, (int, float)) for v in x):
        return out  # pit strategy lists etc.
    else:
        out[p] = x
    return out


def load_setup(path: Path) -> Dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    flat = _flat(raw)
    return {"path": str(path), "name": Path(path).stem, "car": raw.get("carName"),
            "mtime": Path(path).stat().st_mtime, "values": flat}


def physical(setup: Dict[str, Any]) -> Dict[str, Any]:
    """Physical values the file stores directly, plus labelled conventions."""
    v = setup["values"]
    out: Dict[str, Any] = {}
    if "basicSetup.alignment.staticCamber" in v:
        out["camber_deg"] = dict(zip(WHEELS, [round(x, 2) for x in v["basicSetup.alignment.staticCamber"]]))
    if "basicSetup.tyres.tyrePressure" in v:
        out["cold_pressure_psi_convention"] = dict(zip(WHEELS, [round(20.3 + 0.1 * x, 1) for x in v["basicSetup.tyres.tyrePressure"]]))
    if "basicSetup.strategy.fuel" in v:
        out["fuel_l"] = v["basicSetup.strategy.fuel"]
    rh = v.get("advancedSetup.aeroBalance.rideHeight")
    if rh and len(rh) == 4:
        out["ride_height_clicks"] = rh
    return out


def diff_setups(a: Dict[str, Any], b: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Click differences b − a with what each change does (car-independent meaning)."""
    rows = []
    va, vb = a["values"], b["values"]
    for k in sorted(set(va) | set(vb)):
        x, y = va.get(k), vb.get(k)
        if x == y or isinstance(x, str) or isinstance(y, str):
            continue
        info = FIELD_INFO.get(k, {})
        if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            d = [round(q - p, 6) for p, q in zip(x, y)]
        elif isinstance(x, (int, float)) and isinstance(y, (int, float)):
            d = round(y - x, 6)
        else:
            d = None
        rows.append({"field": k, "label": info.get("label", k.split(".")[-1]), "group": info.get("group", "other"),
                     "from": x, "to": y, "delta": d, "increase_means": info.get("increase"),
                     "unit_note": info.get("unit_note") or info.get("unit")})
    return rows


def library(setups_dir: Path, car: str, track: Optional[str] = None) -> List[Dict[str, Any]]:
    """Setup files for a car (and track folder, case-insensitive), newest first."""
    base = Path(setups_dir) / car
    if not base.is_dir():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir()]
    if track:
        dirs = [d for d in dirs if d.name.lower().replace(" ", "_") == track.lower()] or dirs
    files = [f for d in dirs for f in d.glob("*.json")]
    return sorted((load_setup(f) for f in files), key=lambda s: -s["mtime"])


def candidates_for_session(setups_dir: Path, car: str, track: str, session_epoch: Optional[float]) -> List[Dict[str, Any]]:
    """Likely setups for one of your sessions: saved before the session started, newest first.
    Unconfirmed until you say which one you drove."""
    lib = library(setups_dir, car, track)
    if session_epoch is None:
        return lib[:5]
    before = [s for s in lib if s["mtime"] <= session_epoch + 60]
    return (before or lib)[:5]
