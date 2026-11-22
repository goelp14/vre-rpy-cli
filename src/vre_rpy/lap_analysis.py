"""Lap analysis core — any car, any track, any pair of replays.

- Distance-aligned laps on fixed stations; time gap to a reference; variance cliffs.
- Theoretical best from per-segment bests, sanity-gated.
- Corner windows from brake point to next brake point (reference lap), per-corner per-lap events,
  consistency and correlation with section time.
- Car state (car-agnostic, from the data itself): mass, weight split, wheelbase, aero load and balance,
  load transfer, understeer gradient, axle slip angles.

Every number here comes from decoded replay data; thresholds are relative (fractions of pedal,
slip ratios, multiples of the driver's own values), so nothing is tuned to one car or track.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

STATION_M = 2.0
SEGMENT_M = 50.0
G = 9.81
CORNERS4 = ("LF", "RF", "LR", "RR")


# ----------------------------------------------------------------------------- lap on stations
def lap_on_stations(fr: Dict[str, Any], length_m: float, step: float = STATION_M) -> Dict[str, np.ndarray]:
    """Resample one lap's frames onto distance stations 0..length (from the lap's own S/F)."""
    # frames start at the timer's S/F crossing: distances here are metres from the S/F line
    s = np.asarray(fr["s_m"], float)
    s = np.maximum.accumulate(s - s[0])
    st = np.arange(0.0, min(length_m, s[-1]) - 1e-6, step)
    out: Dict[str, np.ndarray] = {"s": st}
    for k in ("t", "speed_kmh", "throttle", "throttle_masked", "brake", "steer_deg", "gear", "g_lat", "g_lon",
              "g_vert", "slip_deg", "yaw_deg", "lat_m", "y", "edge_any_left_m", "edge_any_right_m",
              "edge_all4_left_m", "edge_all4_right_m", "lap_valid_so_far", "rpm"):
        if k in fr and fr[k] is not None and len(fr[k]) == len(s):
            v = np.asarray([np.nan if x is None else x for x in fr[k]], float)
            ok = np.isfinite(v)
            if ok.sum() >= 2:
                out[k] = np.interp(st, s[ok], v[ok])
    for grp in ("tyre_load", "slip_ratio"):
        if isinstance(fr.get(grp), dict) and fr[grp]:
            for c, arr in fr[grp].items():
                v = np.asarray([np.nan if x is None else x for x in arr], float)
                ok = np.isfinite(v)
                if ok.sum() >= 2:
                    out[f"{grp}_{c}"] = np.interp(st, s[ok], v[ok])
    return out


def delta_to_ref(lap: Dict[str, np.ndarray], ref: Dict[str, np.ndarray]) -> np.ndarray:
    n = min(len(lap["s"]), len(ref["s"]))
    return lap["t"][:n] - ref["t"][:n]


def variance_cliffs(delta: np.ndarray, *, step=STATION_M, window_m=120.0, top=5) -> List[Dict[str, Any]]:
    """Distance windows where the time gap grows fastest (steepest loss), non-overlapping."""
    w = max(1, int(window_m / step))
    gain = delta[w:] - delta[:-w]
    order = np.argsort(gain)[::-1]
    picked: List[Dict[str, Any]] = []
    for i in order:
        if gain[i] <= 0.01 or len(picked) >= top:
            break
        if any(abs(i - p["_i"]) < w for p in picked):
            continue
        picked.append({"_i": int(i), "s0_m": round(i * step, 1), "s1_m": round((i + w) * step, 1),
                       "loss_s": round(float(gain[i]), 3)})
    for p in picked:
        p.pop("_i")
    return picked


def theoretical_best(laps: List[Dict[str, np.ndarray]], lap_ms: List[int], length_m: float) -> Dict[str, Any]:
    """Sum of best per-segment times over laps; drops laps outside 0.9-1.3x median; gated."""
    if not laps:
        return {"ok": False, "reason": "no laps"}
    med = float(np.median(lap_ms))
    use = [(l, ms) for l, ms in zip(laps, lap_ms) if 0.9 * med <= ms <= 1.3 * med and len(l["s"]) > 10]
    if not use:
        return {"ok": False, "reason": "no laps within 0.9-1.3x median"}
    edges = np.arange(0.0, length_m, SEGMENT_M)
    best = np.full(len(edges) - 1, np.inf)
    for l, _ in use:
        t = np.interp(edges, l["s"], l["t"], right=np.nan)
        seg = np.diff(t)
        ok = np.isfinite(seg) & (seg > 0)
        best[ok] = np.minimum(best[ok], seg[ok])
    if not np.all(np.isfinite(best)):
        return {"ok": False, "reason": "a segment has no data on any lap"}
    tail = min(ms for _, ms in use) / 1000.0 - float(np.interp(edges[-1], use[0][0]["s"], use[0][0]["t"]))
    theo = float(best.sum() + max(tail, 0.0))
    best_lap = min(ms for _, ms in use) / 1000.0
    gate = 0.95 * best_lap <= theo <= best_lap + 0.05
    return {"ok": bool(gate), "theoretical_best_s": round(theo, 3), "best_lap_s": round(best_lap, 3),
            "potential_s": round(best_lap - theo, 3), "laps_used": len(use),
            "reason": None if gate else "failed sanity gate (theoretical must be within 5% below best)"}


# ----------------------------------------------------------------------------- corner windows
def brake_points(ref: Dict[str, np.ndarray], corners: Sequence[Dict[str, Any]], length_m: float) -> List[float]:
    """Per corner: where the reference lap starts braking for it (or None if flat)."""
    out = []
    b = ref.get("brake")
    for c in corners:
        apex = c.get("apex_m", c.get("apex_spline", 0) * length_m)
        lo = max(0.0, apex - 400.0)
        m = (ref["s"] >= lo) & (ref["s"] <= apex)
        on = None
        if b is not None and m.any():
            idx = np.nonzero(m & (b > 0.10))[0]
            if len(idx):
                # first braking run that reaches the apex region
                on = float(ref["s"][idx[0]])
        out.append(on)
    return out


def corner_windows(ref: Dict[str, np.ndarray], corners: Sequence[Dict[str, Any]], length_m: float) -> List[Dict[str, Any]]:
    """Brake-to-brake windows: [brake point of corner i − 40 m, brake point of next braked corner − 40 m)."""
    bp = brake_points(ref, corners, length_m)
    starts = []
    for c, b in zip(corners, bp):
        apex = c.get("apex_m", c.get("apex_spline", 0) * length_m)
        starts.append((b if b is not None else max(0.0, apex - 120.0)) - 40.0)
    wins = []
    for i, c in enumerate(corners):
        s0 = max(0.0, starts[i])
        s1 = starts[i + 1] if i + 1 < len(corners) else length_m - 1.0
        if s1 <= s0 + 30:
            s1 = s0 + 30
        wins.append({"corner": c.get("display_name") or c.get("name"), "index": i, "s0_m": round(s0, 1),
                     "s1_m": round(min(s1, length_m - 1.0), 1),
                     "apex_m": c.get("apex_m", c.get("apex_spline", 0) * length_m),
                     "direction": c.get("direction"), "inside_edge": c.get("inside_edge"),
                     "braked": bp[i] is not None,
                     "speed_class": c.get("speed_class")})
    return wins


def _first(mask: np.ndarray) -> Optional[int]:
    k = np.nonzero(mask)[0]
    return int(k[0]) if len(k) else None


def corner_events(lap: Dict[str, np.ndarray], win: Dict[str, Any], terrain: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Events inside one brake-to-brake window of one lap."""
    s = lap["s"]
    m = (s >= win["s0_m"]) & (s < win["s1_m"])
    idx = np.nonzero(m)[0]
    if len(idx) < 10:
        return {"ok": False}
    ss = s[idx]
    v = lap["speed_kmh"][idx]
    b = lap.get("brake", np.zeros(len(s)))[idx]
    th = lap.get("throttle_masked", lap.get("throttle", np.zeros(len(s))))[idx]
    st = np.abs(lap.get("steer_deg", np.zeros(len(s)))[idx])
    t = lap["t"][idx]
    ev: Dict[str, Any] = {"ok": True, "section_time_s": round(float(t[-1] - t[0]), 3)}
    apex_k = int(np.argmin(np.abs(ss - win["apex_m"])))
    k_vmin = int(np.argmin(v))
    ev["vmin_kmh"] = round(float(v[k_vmin]), 1)
    ev["vmin_m"] = round(float(ss[k_vmin]), 1)
    ev["vmin_vs_apex_m"] = round(float(ss[k_vmin] - win["apex_m"]), 1)
    k_on = _first(b > 0.10)
    if k_on is not None and k_on < k_vmin + 5:
        ev["brake_on_m"] = round(float(ss[k_on]), 1)
        ev["brake_on_kmh"] = round(float(v[k_on]), 1)
        k_full = _first((b > 0.95) & (np.arange(len(b)) >= k_on))
        if k_full is not None:
            ev["brake_full_m"] = round(float(ss[k_full]), 1)
            ev["brake_ramp_m"] = round(float(ss[k_full] - ss[k_on]), 1)
            ev["brake_ramp_s"] = round(float(t[k_full] - t[k_on]), 3)
        k_peak = k_on + int(np.argmax(b[k_on : k_vmin + 1])) if k_vmin >= k_on else k_on
        rel = _first((b < 0.05) & (np.arange(len(b)) > k_peak))
        if rel is not None:
            ev["brake_off_m"] = round(float(ss[rel]), 1)
        # re-application: brake rises again by > 0.15 after falling, before the slowest point
        seg = b[k_peak : max(k_vmin, k_peak + 1) + 1]
        low = np.minimum.accumulate(seg)
        ev["rebrake_peak"] = round(float(np.max(seg - low)) if len(seg) else 0.0, 3)
        # decel from speed (g), mean over the braking run
        brk = np.arange(k_on, (rel if rel is not None else k_vmin) + 1)
        if len(brk) > 3:
            vv = v[brk] / 3.6
            dec = -np.gradient(vv ** 2 / 2, ss[brk]) / G
            ev["decel_mean_g"] = round(float(np.nanmean(dec)), 3)
            ev["decel_peak_g"] = round(float(np.nanpercentile(dec, 90)), 3)
    # turn-in: steering passes 30 % of this corner's peak lock
    peak_st = float(np.max(st)) if len(st) else 0.0
    k_ti = _first(st > 0.3 * peak_st) if peak_st > 5 else None
    if k_ti is not None:
        ev["turn_in_m"] = round(float(ss[k_ti]), 1)
        ev["turn_in_kmh"] = round(float(v[k_ti]), 1)
        ev["brake_at_turn_in"] = round(float(b[k_ti]), 3)
        ev["peak_steer_deg"] = round(peak_st, 1)
    # throttle: pickup after the slowest point region, and full throttle that stays
    k_pick = _first((th > 0.20) & (np.arange(len(th)) >= max(k_vmin - 15, 0)))
    if k_pick is not None:
        ev["throttle_on_m"] = round(float(ss[k_pick]), 1)
        for k in np.nonzero((th > 0.95) & (np.arange(len(th)) >= k_pick))[0]:
            stay = (ss >= ss[k]) & (ss <= ss[k] + 100.0)
            if np.all(th[stay] > 0.85):
                ev["full_throttle_m"] = round(float(ss[k]), 1)
                ev["full_throttle_kmh"] = round(float(v[k]), 1)
                break
        after = th[k_pick:]
        drops = np.sum((after[:-1] > 0.6) & (after[1:] < after[:-1] - 0.3))
        ev["throttle_lifts"] = int(drops)
    if "brake_off_m" in ev and "throttle_on_m" in ev:
        a = np.searchsorted(ss, ev["brake_off_m"])
        c = np.searchsorted(ss, ev["throttle_on_m"])
        if c > a:
            ev["coast_s"] = round(float(t[c] - t[a]), 3)
    k_str = _first((st < 0.2 * max(peak_st, 1)) & (np.arange(len(st)) > (k_ti or 0) + 5) & (ss > win["apex_m"]))
    if k_str is not None:
        ev["wheel_straight_m"] = round(float(ss[k_str]), 1)
    if "slip_deg" in lap:
        sl = np.abs(lap["slip_deg"][idx])
        ev["peak_slip_deg"] = round(float(np.nanpercentile(sl, 95)), 2)
    inside = win.get("inside_edge") or ("right" if win.get("direction") == "right" else "left")
    outside = "left" if inside == "right" else "right"
    if f"edge_any_{inside}_m" in lap:
        near = np.abs(ss - win["apex_m"]) < 25
        if near.any():
            ev["apex_margin_m"] = round(float(-np.nanmax(lap[f"edge_any_{inside}_m"][idx][near])), 2)
        post = ss > win["apex_m"]
        if post.any():
            ev["exit_margin_m"] = round(float(-np.nanmax(lap[f"edge_any_{outside}_m"][idx][post])), 2)
    if "slip_ratio_LR" in lap:
        rear = np.maximum(lap["slip_ratio_LR"][idx], lap["slip_ratio_RR"][idx])
        front = np.minimum(lap["slip_ratio_LF"][idx], lap["slip_ratio_RF"][idx])
        post = np.arange(len(idx)) >= k_vmin
        ev["exit_wheelspin_max"] = round(float(np.nanmax(rear[post])), 3) if post.any() else None
        ev["entry_lock_min"] = round(float(np.nanmin(front[: k_vmin + 1])), 3)
    # terrain context: what the car was doing at each crest / dip inside the window
    tctx = []
    for f in terrain:
        if win["s0_m"] <= f["peak_m"] < win["s1_m"]:
            k = int(np.argmin(np.abs(ss - f["peak_m"])))
            row = {"kind": f["kind"], "at_m": f["peak_m"], "brake": round(float(b[k]), 2),
                   "throttle": round(float(th[k]), 2), "steer_deg": round(float(st[k]), 1),
                   "speed_kmh": round(float(v[k]), 1),
                   "expected_dg": round(float(f.get("dg_at_100kmh", 0)) * (float(v[k]) / 100.0) ** 2, 3)}
            if "g_vert" in lap:
                row["g_vert"] = round(float(lap["g_vert"][idx][k]), 3)
            tctx.append(row)
    if tctx:
        ev["terrain"] = tctx
    return ev


def consistency(rows: List[Dict[str, Any]], keys: Sequence[str]) -> Dict[str, Any]:
    """Per metric: median, IQR, n, and correlation with section time (flag when |r| >= 0.4, n >= 8)."""
    out = {}
    times = np.array([r.get("section_time_s", np.nan) for r in rows], float)
    for k in keys:
        vals = np.array([r.get(k, np.nan) if r.get(k) is not None else np.nan for r in rows], float)
        ok = np.isfinite(vals)
        if ok.sum() < 3:
            continue
        q1, q3 = np.percentile(vals[ok], [25, 75])
        both = ok & np.isfinite(times)
        r = float(np.corrcoef(vals[both], times[both])[0, 1]) if both.sum() >= 5 and np.std(vals[both]) > 0 else None
        out[k] = {"median": round(float(np.median(vals[ok])), 3), "iqr": round(float(q3 - q1), 3),
                  "n": int(ok.sum()), "r_with_time": None if r is None else round(r, 3),
                  "matters": bool(r is not None and abs(r) >= 0.4 and both.sum() >= 8)}
    return out


METRICS = ("brake_on_m", "brake_ramp_m", "decel_mean_g", "rebrake_peak", "brake_at_turn_in", "turn_in_kmh",
           "vmin_kmh", "vmin_vs_apex_m", "throttle_on_m", "full_throttle_m", "coast_s", "throttle_lifts",
           "wheel_straight_m", "peak_slip_deg", "apex_margin_m", "exit_margin_m", "exit_wheelspin_max",
           "entry_lock_min")


# ----------------------------------------------------------------------------- car state (S2)
def car_state(rf, laps_st: List[Dict[str, np.ndarray]]) -> Dict[str, Any]:
    """Car parameters and handling state estimated from the data (no car database needed)."""
    inp = rf.inputs
    out: Dict[str, Any] = {}
    loads = inp.get("tyre_load") or {}
    v = np.asarray(inp["speed_kmh"], float) / 3.6
    if loads:
        tl = {c: np.asarray(loads[c], float) for c in CORNERS4 if c in loads}
        tot = sum(tl.values())
        still = (v < 0.3) & (tot > 1000.0)
        if still.sum() > 20:
            out["mass_kg"] = round(float(np.median(tot[still]) / G), 1)
            out["static_front_share"] = round(float(np.median((tl["LF"] + tl["RF"])[still] / tot[still])), 3)
        straight = (v > 25) & (np.abs(np.asarray(inp["g_lat"])) < 0.08) & (np.abs(np.asarray(inp["g_lon"])) < 0.15) & (np.asarray(inp["brake"]) < 0.01)
        if straight.sum() > 200:
            a = np.c_[np.full(straight.sum(), G), v[straight] ** 2]
            coef, *_ = np.linalg.lstsq(a, tot[straight], rcond=None)
            cf, *_ = np.linalg.lstsq(a, (tl["LF"] + tl["RF"])[straight], rcond=None)
            out["aero_load_coeff_n_per_ms2"] = round(float(coef[1]), 3)
            out["downforce_at_200kmh_n"] = round(float(coef[1] * (200 / 3.6) ** 2), 0)
            out["aero_front_share"] = round(float(cf[1] / coef[1]), 3) if coef[1] > 0 else None
        # lateral load transfer per g: (outside - inside)/total vs |g_lat|
        gl = np.asarray(inp["g_lat"], float)
        corner = (np.abs(gl) > 0.5) & (v > 15)
        if corner.sum() > 200:
            left = tl["LF"] + tl["LR"]
            right = tl["RF"] + tl["RR"]
            ltr = (right - left) / np.maximum(tot, 1)
            k = np.polyfit(gl[corner], ltr[corner], 1)[0]
            out["lat_transfer_per_g"] = round(float(abs(k)), 3)
            fr_ = (tl["RF"] - tl["LF"]) / np.maximum(tl["LF"] + tl["RF"], 1)
            rr_ = (tl["RR"] - tl["LR"]) / np.maximum(tl["LR"] + tl["RR"], 1)
            kf = abs(np.polyfit(gl[corner], fr_[corner], 1)[0])
            kr = abs(np.polyfit(gl[corner], rr_[corner], 1)[0])
            out["front_lat_transfer_share"] = round(float(kf / max(kf + kr, 1e-9)), 3)
        brake = (np.asarray(inp["brake"]) > 0.5) & (v > 20) & (np.abs(gl) < 0.3)
        if brake.sum() > 100:
            fshare = (tl["LF"] + tl["RF"]) / np.maximum(tot, 1)
            out["front_share_under_braking"] = round(float(np.median(fshare[brake])), 3)
    wb = rf.pose.get("wheels")
    if wb is not None:
        f = wb[:, 0:2, :].mean(1)
        r = wb[:, 2:4, :].mean(1)
        out["wheelbase_m"] = round(float(np.median(np.linalg.norm(f - r, axis=1))), 3)
        out["front_track_m"] = round(float(np.median(np.linalg.norm(wb[:, 0] - wb[:, 1], axis=1))), 3)
        out["rear_track_m"] = round(float(np.median(np.linalg.norm(wb[:, 2] - wb[:, 3], axis=1))), 3)
    # understeer gradient and axle slip angles from frames (deg of extra steer per g)
    ua_all, ay_all, af, ar = [], [], [], []
    L = out.get("wheelbase_m")
    fs = out.get("static_front_share")
    for lap in laps_st:
        if "slip_deg" not in lap or L is None:
            continue
        vv = lap["speed_kmh"] / 3.6
        ay = lap["g_lat"]
        yaw = np.unwrap(np.radians(lap["yaw_deg"])) if "yaw_deg" in lap else None
        if yaw is None:
            continue
        dt = np.gradient(lap["t"])
        r_ = np.gradient(yaw) / np.maximum(dt, 1e-3)
        steer_ratio = 11.0
        delta = np.radians(lap["steer_deg"] / steer_ratio)
        beta = np.radians(lap["slip_deg"])
        ok = (vv > 15) & (np.abs(ay) > 0.4)
        ua = np.sign(delta) * (delta - r_ * L / np.maximum(vv, 1))
        ua_all.append(np.degrees(ua[ok]))
        ay_all.append(np.abs(ay[ok]))
        if fs:
            a_, b_ = (1 - fs) * L, fs * L   # CG to front / rear axle
            af.append(np.degrees(np.abs(delta - beta - a_ * r_ / np.maximum(vv, 1)))[ok])
            ar.append(np.degrees(np.abs(-beta + b_ * r_ / np.maximum(vv, 1)))[ok])
    if ua_all:
        ua_c, ay_c = np.concatenate(ua_all), np.concatenate(ay_all)
        fin = np.isfinite(ua_c) & np.isfinite(ay_c)
        if fin.sum() > 100:
            k = np.polyfit(ay_c[fin], ua_c[fin], 1)
            out["understeer_gradient_deg_per_g"] = round(float(k[0]), 2)
            out["understeer_note"] = "steering ratio assumed 11:1; compare drivers/setups, not absolute values"
    if af:
        out["front_axle_slip_deg_median"] = round(float(np.nanmedian(np.concatenate(af))), 2)
        out["rear_axle_slip_deg_median"] = round(float(np.nanmedian(np.concatenate(ar))), 2)
    return out


# ----------------------------------------------------------------------------- driver report
def analyze_driver(rf, *, windows=None, terrain=(), laps: str = "valid_flying") -> Dict[str, Any]:
    sel = [l for l in rf.laps if l.get("completed") and l.get("flying_candidate") and (l.get("valid") or laps == "all_flying")]
    frames = []
    for l in sel:
        try:
            fr = rf.lap_frames(l["lap_index"])
        except ValueError:
            continue
        if fr["meta"]["sync"].get("lap_sync_go") and not fr["meta"].get("partial"):
            frames.append(fr)
    L = rf.length_m
    st = [lap_on_stations(fr, L) for fr in frames]
    # where the AI line's origin sits relative to the S/F line (frames start at the crossing)
    s0 = [float(np.asarray(fr["s_m"], float)[0]) for fr in frames]
    sf_off = float(np.median(np.mod(s0, L))) if s0 else 0.0
    lap_ms = [fr["meta"]["lap_ms"] for fr in frames]
    best_i = int(np.argmin(lap_ms)) if lap_ms else None
    return {"label": getattr(rf, "label", rf.path.stem), "identity": rf.ident, "car": rf.car,
            "lap_ms": lap_ms, "lap_indices": [fr["meta"]["lap_index"] for fr in frames],
            "best_index": best_i, "stations": st, "frames": frames,
            "theoretical": theoretical_best(st, lap_ms, L), "length_m": L, "sf_offset_m": sf_off}


def pack_from_sf(pack: Optional[Dict[str, Any]], sf_offset_m: float, length_m: float) -> Dict[str, Any]:
    """Track-pack positions are along the AI line from its origin; re-express from the S/F line."""
    if not pack:
        return {}
    def f(m):
        return float(np.mod(m - sf_offset_m, length_m))
    corners = []
    for c in pack.get("corners") or []:
        c2 = dict(c)
        for k in ("apex_m", "start_m", "end_m"):
            if k in c2:
                c2[k] = round(f(c2[k]), 1)
        corners.append(c2)
    terrain = []
    for t in pack.get("terrain") or []:
        t2 = dict(t)
        for k in ("peak_m", "start_m", "end_m"):
            if k in t2:
                t2[k] = round(f(t2[k]), 1)
        terrain.append(t2)
    return {**pack, "corners": sorted(corners, key=lambda c: c["apex_m"]),
            "terrain": sorted(terrain, key=lambda t: t["peak_m"]), "sf_offset_m": sf_offset_m}


def compare(me: Dict[str, Any], ref: Optional[Dict[str, Any]], pack: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Full analysis: per-corner events for every lap of both drivers, consistency, gaps, cliffs.

    ``pack`` positions must already be S/F-relative (``pack_from_sf``)."""
    L = me["length_m"]
    corners = (pack or {}).get("corners") or []
    terrain = (pack or {}).get("terrain") or []
    base = ref if ref and ref.get("best_index") is not None else me
    if base.get("best_index") is None:
        return {"ok": False, "reason": "no valid flying lap with good sync"}
    ref_best = base["stations"][base["best_index"]]
    wins = corner_windows(ref_best, corners, L) if corners else []
    out: Dict[str, Any] = {"ok": True, "length_m": L, "windows": wins, "drivers": {}}
    for d in (me, ref) if ref else (me,):
        if d is None or d.get("best_index") is None:
            continue
        per_corner = {}
        for w in wins:
            rows = []
            for k, lap in enumerate(d["stations"]):
                ev = corner_events(lap, w, terrain)
                if ev.get("ok"):
                    ev["lap_index"] = d["lap_indices"][k]
                    rows.append(ev)
            per_corner[w["corner"] + f"#{w['index']}"] = {"laps": rows, "consistency": consistency(rows, METRICS),
                                                          "best_lap": next((r for r in rows if r["lap_index"] == d["lap_indices"][d["best_index"]]), None)}
        out["drivers"][d["label"]] = {"lap_ms": d["lap_ms"], "best_lap_ms": min(d["lap_ms"]),
                                      "theoretical": d["theoretical"], "corners": per_corner}
    if ref and ref.get("best_index") is not None and me.get("best_index") is not None:
        mb, rb = me["stations"][me["best_index"]], ref["stations"][ref["best_index"]]
        dlt = delta_to_ref(mb, rb)
        out["delta_best_vs_ref"] = {"step_m": STATION_M, "delta_s": [round(float(x), 3) for x in dlt[::5]],
                                    "final_s": round(float(dlt[-1]), 3)}
        out["variance_cliffs"] = variance_cliffs(dlt)
        sec = []
        for w in wins:
            a, b = int(w["s0_m"] / STATION_M), int(min(w["s1_m"], (len(dlt) - 1) * STATION_M) / STATION_M)
            if b > a:
                sec.append({"corner": w["corner"], "index": w["index"], "loss_s": round(float(dlt[b] - dlt[a]), 3)})
        out["corner_loss_best_vs_ref"] = sorted(sec, key=lambda r: -r["loss_s"])
    return out


def run_analysis(me_rf, ref_rf=None) -> Dict[str, Any]:
    """Everything the coaching layer needs, JSON-ready."""
    from .track_pack_gen import load_track_pack

    me = analyze_driver(me_rf)
    ref = analyze_driver(ref_rf) if ref_rf is not None else None
    pack = load_track_pack(str(me_rf.ident.get("track") or ""))
    pack_sf = pack_from_sf(pack, me["sf_offset_m"], me["length_m"]) if pack else None
    out = compare(me, ref, pack_sf)
    out["schema"] = "vre.acc_rpy.analysis.v1"
    out["track"] = me_rf.ident.get("track")
    out["car_state"] = {me["label"]: car_state(me_rf, me["stations"])}
    if ref is not None:
        out["car_state"][ref["label"]] = car_state(ref_rf, ref["stations"])
    out["pack"] = {"corners": (pack_sf or {}).get("corners"), "terrain": (pack_sf or {}).get("terrain"),
                   "sf_offset_m": me["sf_offset_m"], "notes": (pack or {}).get("notes")}
    out["labels"] = {"me": me["label"], "ref": ref["label"] if ref else None}
    out["distance_origin"] = "metres from the S/F line"
    return out
