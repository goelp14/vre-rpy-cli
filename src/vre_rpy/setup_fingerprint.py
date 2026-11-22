"""Setup fingerprint from a replay — what the setup *does*, measured from the data (any car).

A replay does not store the setup (the whole file is accounted for by the loader). So this reports:
- exact values: static mass, gearing (km/h per 1000 rpm per gear), tyre radii, car dimensions;
- estimated, setup-sensitive behaviour, each with a lap-to-lap spread: aero load and balance, brake
  balance (front vs rear slip under straight braking), roll-stiffness split, pitch under braking,
  rake change with speed, diff behaviour (inside-rear spin on exit), TC/ABS estimate rates,
  understeer gradient.
Estimates stay "uncalibrated" until sessions with a confirmed setup (``Calibration``) show how a setting
moves them for that car; they are never written out as a loadable setup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

G = 9.81
C4 = ("LF", "RF", "LR", "RR")


def _per_lap(rf, fn) -> List[float]:
    """Apply fn(mask) per completed flying lap on the input clock; keep finite results."""
    it = np.asarray(rf.inputs["time_s"], float)
    vals = []
    for l in rf.laps:
        if not (l.get("completed") and l.get("flying_candidate")):
            continue
        t0 = l.get("sf_time_s", l["session_start_s"])
        m = (it >= t0) & (it < t0 + l["lap_ms"] / 1000.0)
        if m.sum() < 200:
            continue
        v = fn(m)
        if v is not None and np.isfinite(v):
            vals.append(float(v))
    return vals


def _stat(vals: List[float], *, status="uncalibrated", unit="", note="") -> Optional[Dict[str, Any]]:
    if not vals:
        return None
    a = np.asarray(vals)
    return {"value": round(float(np.median(a)), 4), "spread": round(float(np.std(a)) if len(a) > 1 else float("nan"), 4),
            "laps": len(a), "status": status, "unit": unit, "note": note}


def fingerprint(rf) -> Dict[str, Any]:
    inp = rf.inputs
    v = np.asarray(inp["speed_kmh"], float) / 3.6
    gl = np.asarray(inp["g_lat"], float)
    glon = np.asarray(inp["g_lon"], float)
    brk = np.asarray(inp["brake"], float)
    thr = np.asarray(inp["throttle"], float)
    loads = {c: np.asarray(inp["tyre_load"][c], float) for c in C4} if inp.get("tyre_load") else {}
    slips = {c: np.asarray(inp["slip_ratio"][c], float) for c in C4} if inp.get("slip_ratio") else {}
    sus = {c: np.asarray(inp["sus_proxy"][c], float) for c in C4} if inp.get("sus_proxy") else {}
    fp: Dict[str, Any] = {"exact": {}, "estimated": {}}

    # exact: gearing, radii, dimensions, static mass
    rpm = np.asarray(inp["rpm"], float)
    gear = np.asarray(inp["gear"], float)
    gearing = {}
    for g in range(1, 8):
        m = (gear == g) & (rpm > 3000) & (v > 5) & (thr > 0.5)
        if m.sum() > 50:
            gearing[str(g)] = round(float(np.median(v[m] * 3.6 / rpm[m] * 1000.0)), 2)
    fp["exact"]["kmh_per_1000rpm_by_gear"] = gearing
    fp["exact"]["tyre_radius_m"] = inp.get("wheel_radius_m")
    if loads:
        tot = sum(loads.values())
        still = (v < 0.3) & (tot > 1000)
        if still.sum() > 20:
            fp["exact"]["static_mass_kg"] = round(float(np.median(tot[still]) / G), 1)
            fp["exact"]["static_front_share"] = round(float(np.median((loads["LF"] + loads["RF"])[still] / tot[still])), 3)

    est = fp["estimated"]
    if loads:
        tot = sum(loads.values())
        front = loads["LF"] + loads["RF"]

        def aero(m, which):
            s = m & (v > 25) & (np.abs(gl) < 0.08) & (np.abs(glon) < 0.15) & (brk < 0.01)
            if s.sum() < 40:
                return None
            a = np.c_[np.full(s.sum(), G), v[s] ** 2]
            c, *_ = np.linalg.lstsq(a, tot[s], rcond=None)
            if which == "coef":
                return c[1]
            cf, *_ = np.linalg.lstsq(a, front[s], rcond=None)
            return cf[1] / c[1] if c[1] > 0 else None

        est["aero_load_coeff"] = _stat(_per_lap(rf, lambda m: aero(m, "coef")), unit="N/(m/s)^2",
                                       note="downforce = coeff × v²; wing / ride height / rake")
        est["aero_front_share"] = _stat(_per_lap(rf, lambda m: aero(m, "front")), unit="0-1",
                                        note="share of downforce on the front axle")
    if slips:
        def bb(m):
            s = m & (brk > 0.6) & (np.abs(gl) < 0.3) & (v > 25)
            if s.sum() < 15:
                return None
            f = -(slips["LF"][s] + slips["RF"][s]) / 2
            r = -(slips["LR"][s] + slips["RR"][s]) / 2
            return float(np.median(f) / max(np.median(f) + np.median(r), 1e-4))

        est["brake_slip_front_share"] = _stat(_per_lap(rf, bb), unit="0-1",
                                              note="front share of braking slip on straight braking; ↑ = more front bias")

        def diff(m):
            s = m & (thr > 0.6) & (np.abs(gl) > 0.5) & (v > 10)
            if s.sum() < 15:
                return None
            right_turn = gl[s] < 0  # sign convention follows the decoder; inside = lighter rear wheel
            inner = np.where(loads["LR"][s] < loads["RR"][s], slips["LR"][s], slips["RR"][s]) if loads else slips["LR"][s]
            outer = np.where(loads["LR"][s] < loads["RR"][s], slips["RR"][s], slips["LR"][s]) if loads else slips["RR"][s]
            _ = right_turn
            return float(np.median(inner - outer))

        est["diff_inside_spin"] = _stat(_per_lap(rf, diff), unit="slip ratio",
                                        note="inside-rear minus outside-rear slip on throttle in corners; ↓ = more locking")
        tc = lambda m: float(np.mean(((np.maximum(slips["LR"], slips["RR"]) > 0.08) & (thr > 0.3))[m]))
        ab = lambda m: float(np.mean(((np.minimum(slips["LF"], slips["RF"]) < -0.08) & (brk > 0.3))[m]))
        est["tc_estimate_rate"] = _stat(_per_lap(rf, tc), unit="fraction of lap", note="slip-based estimate, not TC state")
        est["abs_estimate_rate"] = _stat(_per_lap(rf, ab), unit="fraction of lap", note="slip-based estimate, not ABS state")
    if sus:
        def roll(m):
            s = m & (np.abs(gl) > 0.5) & (v > 15)
            if s.sum() < 30:
                return None
            kf = abs(np.polyfit(gl[s], sus["LF"][s] - sus["RF"][s], 1)[0])
            kr = abs(np.polyfit(gl[s], sus["LR"][s] - sus["RR"][s], 1)[0])
            # less roll travel on an axle = stiffer: stiffness share ∝ 1/travel
            return float((1 / max(kf, 1e-6)) / ((1 / max(kf, 1e-6)) + (1 / max(kr, 1e-6))))

        def pitch(m):
            s = m & (brk > 0.3) & (np.abs(gl) < 0.3) & (v > 20)
            if s.sum() < 15:
                return None
            fm = (sus["LF"] + sus["RF"]) / 2
            rm = (sus["LR"] + sus["RR"]) / 2
            return float(np.polyfit(glon[s], (fm - rm)[s], 1)[0])

        def rake(m):
            s = m & (np.abs(gl) < 0.1) & (np.abs(glon) < 0.2) & (v > 15)
            if s.sum() < 40:
                return None
            fm = (sus["LF"] + sus["RF"]) / 2
            rm = (sus["LR"] + sus["RR"]) / 2
            return float(np.polyfit(v[s] ** 2, (rm - fm)[s], 1)[0] * (200 / 3.6) ** 2)

        est["roll_stiffness_front_share"] = _stat(_per_lap(rf, roll), unit="0-1 (proxy)",
                                                  note="from suspension proxy roll per g; ↑ = stiffer front (bar/springs)")
        est["pitch_per_g_braking"] = _stat(_per_lap(rf, pitch), unit="proxy/g", note="front-vs-rear travel per g of braking")
        est["rake_change_at_200kmh"] = _stat(_per_lap(rf, rake), unit="proxy", note="how rake changes with downforce")
    return fp


HINTS = {
    "aero_load_coeff": ("more total downforce", "rear wing / splitter up, ride height down"),
    "aero_front_share": ("more front aero balance", "splitter up, front ride height down, more rake, or less rear wing"),
    "brake_slip_front_share": ("more front braking", "more front brake bias"),
    "diff_inside_spin": ("more inside-rear spin on exit", "less diff preload/locking (or harsher throttle)"),
    "roll_stiffness_front_share": ("stiffer front in roll", "stiffer front bar or springs / softer rear"),
    "pitch_per_g_braking": ("more nose dive under braking", "softer front springs / bump stops"),
    "rake_change_at_200kmh": ("rake grows more with speed", "ride heights / springs / bump stops"),
    "tc_estimate_rate": ("more wheelspin (likely more TC)", "TC level, diff, throttle application"),
    "abs_estimate_rate": ("more front lock (likely more ABS)", "ABS level, brake bias, pressure build"),
}


def compare_fingerprints(a: Dict[str, Any], b: Dict[str, Any], *, labels=("You", "Ref")) -> List[Dict[str, Any]]:
    """b vs a, in plain terms; 'can't tell' when the difference is inside the lap-to-lap spread."""
    rows = []
    for k, (what, levers) in HINTS.items():
        x, y = a["estimated"].get(k), b["estimated"].get(k)
        if not x or not y:
            continue
        d = y["value"] - x["value"]
        noise = np.nanmax([x["spread"], y["spread"], 1e-9])
        verdict = "can't tell" if not np.isfinite(noise) or abs(d) < 2 * noise else (
            f"{labels[1]} shows {what}" if d > 0 else f"{labels[0]} shows {what}")
        rows.append({"item": k, labels[0]: x["value"], labels[1]: y["value"], "difference": round(d, 4),
                     "noise": None if not np.isfinite(noise) else round(float(noise), 4),
                     "verdict": verdict, "likely_levers": levers if verdict != "can't tell" else None,
                     "status": "uncalibrated" if "uncalibrated" in (x["status"], y["status"]) else "calibrated"})
    return rows


class Calibration:
    """Per-car pairs (confirmed setup, fingerprint) → how a click moves each fingerprint item."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.pairs: List[Dict[str, Any]] = json.loads(self.path.read_text()) if self.path.is_file() else []

    def add(self, car: str, setup: Dict[str, Any], fp: Dict[str, Any], session: str) -> None:
        self.pairs = [p for p in self.pairs if p["session"] != session]
        est = {k: v["value"] for k, v in fp["estimated"].items() if v}
        self.pairs.append({"car": car, "session": session, "setup": setup["values"], "setup_name": setup["name"],
                           "fingerprint": est})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.pairs, indent=1, default=float), encoding="utf-8")

    def sensitivities(self, car: str) -> List[Dict[str, Any]]:
        """Fingerprint change per click for setup fields that differ between confirmed sessions."""
        ps = [p for p in self.pairs if p["car"] == car]
        out = []
        if len(ps) < 2:
            return out
        fields = {k for p in ps for k, v in p["setup"].items() if isinstance(v, (int, float))}
        for f in sorted(fields):
            xs = np.array([p["setup"].get(f, np.nan) for p in ps], float)
            if np.nanstd(xs) == 0:
                continue
            for item in HINTS:
                ys = np.array([p["fingerprint"].get(item, np.nan) for p in ps], float)
                ok = np.isfinite(xs) & np.isfinite(ys)
                if ok.sum() >= 2 and np.std(xs[ok]) > 0:
                    k = np.polyfit(xs[ok], ys[ok], 1)[0]
                    out.append({"field": f, "item": item, "per_click": round(float(k), 5), "sessions": int(ok.sum())})
        return out
