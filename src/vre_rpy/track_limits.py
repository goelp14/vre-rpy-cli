"""Track limits and lap validity from replays — any track.

What a replay holds (see README "Track limits"):
- The game's verdict: each lap's first cut drops the lap-valid flag (later cuts in an invalid lap are
  invisible). Offline replays carry no warning/penalty counters.
- ACC often judges a cut later, at fixed places per track (lag 1-2 s or 8-15 s). Where the cut
  actually happened is learnable per track from many laps (``ZoneModel``).
- Wheel positions vs the AI-line edges show every excursion. In quick-judged zones "all four wheels
  past the edge" predicts the verdict well; in delayed zones it does not (the game applies its own
  logic), so excursions are reported as "over the line — not judged", never as illegal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

DROP_BIN_M = 50.0
EXC_BIN_M = 10.0
WINDOW_S = 15.0
ENVELOPE_BIN_M = 10.0


def excursions(frames: Dict[str, Any], *, min_frames: int = 2) -> List[Dict[str, Any]]:
    """Runs of frames with at least one wheel beyond an edge; peak margins per side."""
    if "edge_any_left_m" not in frames:
        return []
    t = np.asarray(frames["t"], float)
    s = np.asarray(frames["s_m"], float)
    out: List[Dict[str, Any]] = []
    for side in ("left", "right"):
        anym = np.asarray(frames[f"edge_any_{side}_m"], float)
        all4 = np.asarray(frames[f"edge_all4_{side}_m"], float)
        over = np.nan_to_num(anym, nan=-99) > 0
        edges = np.diff(np.r_[0, over.astype(int), 0])
        starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
        for a, b in zip(starts, ends):
            if b - a < min_frames:
                continue
            seg = all4[a:b]
            near = np.nonzero(seg >= np.nanmax(seg) - 0.05)[0]  # centre of the near-max plateau
            k = a + int(near[len(near) // 2])
            out.append({
                "side": side,
                "t0": float(t[a]), "t1": float(t[b - 1]),
                "s0_m": float(s[a]), "s1_m": float(s[b - 1]),
                "peak_s_m": float(s[k]), "peak_t": float(t[k]),
                "all4_peak_m": float(all4[k]),         # > 0: all four wheels beyond the edge
                "any_peak_m": float(np.nanmax(anym[a:b])),
                "all4": bool(all4[k] > 0),
            })
    out.sort(key=lambda e: e["t0"])
    return out


@dataclass
class ZoneModel:
    """Per-track map: where the valid flag drops → where the cut was, and the judging delay."""

    track: str
    zones: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # drop bin → {exc_bins: {bin: n}, lags: []}

    def add(self, drop_s: float, exc_s: float, lag_s: float) -> None:
        z = self.zones.setdefault(str(int(drop_s // DROP_BIN_M)), {"exc_bins": {}, "lags": []})
        b = str(int(exc_s // EXC_BIN_M))
        z["exc_bins"][b] = z["exc_bins"].get(b, 0) + 1
        z["lags"].append(round(float(lag_s), 2))

    def vote(self, drop_s: float, exc_s: float) -> int:
        z = self.zones.get(str(int(drop_s // DROP_BIN_M)))
        if not z:
            return 0
        b = int(exc_s // EXC_BIN_M)
        return sum(n for k, n in z["exc_bins"].items() if abs(int(k) - b) <= 1)

    def summary(self) -> List[Dict[str, Any]]:
        out = []
        for k, z in sorted(self.zones.items(), key=lambda kv: int(kv[0])):
            best = max(z["exc_bins"].items(), key=lambda kv: kv[1])
            out.append({
                "drop_m": int(k) * DROP_BIN_M,
                "cut_m": int(best[0]) * EXC_BIN_M,
                "laps": sum(z["exc_bins"].values()),
                "lag_s_median": float(np.median(z["lags"])) if z["lags"] else None,
            })
        return out

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({"track": self.track, "zones": self.zones}, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, track: str) -> "ZoneModel":
        p = Path(path)
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("track") == track:
                return cls(track=track, zones=d.get("zones") or {})
        return cls(track=track)


def _candidates(exc: List[Dict[str, Any]], drop_t: float) -> List[Dict[str, Any]]:
    return [e for e in exc if drop_t - WINDOW_S <= e["peak_t"] <= drop_t + 0.3]


def attribute(lap_frames: Dict[str, Any], exc: List[Dict[str, Any]], model: Optional[ZoneModel]) -> Optional[Dict[str, Any]]:
    """Which excursion the game invalidated this lap for (None for valid laps / no candidate)."""
    meta = lap_frames["meta"]
    inv_ms = meta.get("invalid_at_lap_ms")
    if meta.get("valid") or inv_ms is None:
        return None
    t = np.asarray(lap_frames["t"], float)
    s = np.asarray(lap_frames["s_m"], float)
    drop_t = inv_ms / 1000.0
    drop_s = float(np.interp(drop_t, t, s))
    cands = _candidates(exc, drop_t)
    if not cands:
        return {"drop_m": drop_s, "drop_t": drop_t, "cut_m": None, "method": "no_excursion_found",
                "confidence": "low"}
    if model is not None:
        scored = [(model.vote(drop_s, e["peak_s_m"]), e["all4_peak_m"], e) for e in cands]
        votes, _, best = max(scored, key=lambda x: (x[0], x[1]))
        method = "zone_model" if votes >= 2 else "largest_excursion"
    else:
        votes, best = 0, max(cands, key=lambda e: e["all4_peak_m"])
        method = "largest_excursion"
    lag = drop_t - best["peak_t"]
    conf = "high" if (method == "zone_model" and votes >= 3) or (best["all4"] and lag < 3.0) else "medium"
    return {
        "drop_m": round(drop_s, 1), "drop_t": round(drop_t, 2),
        "cut_m": round(best["peak_s_m"], 1), "cut_t": round(best["peak_t"], 2), "side": best["side"],
        "all4_peak_m": round(best["all4_peak_m"], 2), "judged_after_s": round(lag, 2),
        "method": method, "zone_votes": votes, "confidence": conf,
    }


def learn(model: ZoneModel, laps: Iterable[Dict[str, Any]]) -> None:
    """Two passes: count votes for every candidate, then keep each lap's best-voted excursion."""
    laps = list(laps)
    staged = []
    for fr in laps:
        meta = fr["meta"]
        if meta.get("valid") or meta.get("invalid_at_lap_ms") is None:
            continue
        exc = excursions(fr)
        t = np.asarray(fr["t"], float)
        s = np.asarray(fr["s_m"], float)
        drop_t = meta["invalid_at_lap_ms"] / 1000.0
        drop_s = float(np.interp(drop_t, t, s))
        cands = _candidates(exc, drop_t)
        staged.append((drop_s, drop_t, cands))
    tmp = ZoneModel(model.track)
    for drop_s, drop_t, cands in staged:
        for e in cands:
            if e["any_peak_m"] > -2.5:
                tmp.add(drop_s, e["peak_s_m"], drop_t - e["peak_t"])
    for drop_s, drop_t, cands in staged:
        if not cands:
            continue
        best = max(cands, key=lambda e: (tmp.vote(drop_s, e["peak_s_m"]), e["all4_peak_m"]))
        model.add(drop_s, best["peak_s_m"], drop_t - best["peak_t"])


def legal_envelope(laps: Iterable[Dict[str, Any]], length_m: float) -> Dict[str, Any]:
    """Widest all-four-wheels position seen on track while the lap was still valid, per 10 m."""
    nb = int(np.ceil(length_m / ENVELOPE_BIN_M)) + 2
    env = {"left": np.full(nb, -np.inf), "right": np.full(nb, -np.inf)}
    for fr in laps:
        valid_now = np.asarray(fr.get("lap_valid_so_far") or [], float) > 0.5
        if not len(valid_now) or "edge_all4_left_m" not in fr:
            continue
        s = np.asarray(fr["s_m"], float)
        b = np.clip((np.mod(s, length_m) // ENVELOPE_BIN_M).astype(int), 0, nb - 1)
        for side in ("left", "right"):
            m = np.asarray(fr[f"edge_all4_{side}_m"], float)
            ok = valid_now & np.isfinite(m)
            np.maximum.at(env[side], b[ok], m[ok])
    return {
        "bin_m": ENVELOPE_BIN_M,
        "left": [None if not np.isfinite(v) else round(float(v), 2) for v in env["left"]],
        "right": [None if not np.isfinite(v) else round(float(v), 2) for v in env["right"]],
        "note": "metres beyond the AI-line edge reached by the innermost wheel on still-valid laps",
    }


def analyze(rfs: List[Any], *, zone_model_path: Optional[Path] = None) -> Dict[str, Any]:
    """Track-limit report for several ReplayFrames of the same track."""
    if not rfs:
        return {}
    track = str(rfs[0].ident.get("track"))
    model = ZoneModel.load(zone_model_path, track) if zone_model_path else ZoneModel(track)
    per = []
    for rf in rfs:
        laps = []
        for l in rf.laps:
            if not l.get("completed"):
                continue
            try:
                laps.append(rf.lap_frames(l["lap_index"]))
            except ValueError:
                continue
        per.append((rf, laps))
    learn(model, [fr for _, laps in per for fr in laps])
    if zone_model_path:
        model.save(zone_model_path)
    out_drivers = []
    for rf, laps in per:
        rows = []
        for fr in laps:
            exc = excursions(fr)
            att = attribute(fr, exc, model)
            cut_t = att.get("cut_t") if att else None
            for e in exc:
                e["status"] = ("judged" if cut_t is not None and abs(e["peak_t"] - cut_t) < 0.006
                               else "over the line — not judged")
            rows.append({
                "lap_index": fr["meta"]["lap_index"], "lap_ms": fr["meta"]["lap_ms"],
                "valid": fr["meta"]["valid"], "cut": att, "excursions": exc,
            })
        out_drivers.append({"label": getattr(rf, "label", rf.path.stem), "car": rf.car, "laps": rows})
    length = rfs[0].length_m
    return {
        "schema": "vre.acc_rpy.track_limits.v1",
        "track": track,
        "zones": model.summary(),
        "envelope": legal_envelope([fr for _, laps in per for fr in laps], length),
        "drivers": out_drivers,
        "notes": [
            "The game's verdict (first cut per lap) is exact; cut location is attributed.",
            "Excursions not judged are 'over the line — not judged', never 'illegal'.",
            "Edges are the AI line's official widths, not always the painted white lines.",
        ],
    }
