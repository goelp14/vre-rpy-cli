"""Clocks for replay frames.

Pose records (car position, ~14-27 Hz) carry no timestamp. They are recorded at a constant rate per
file, so their time is recovered by anchoring spline wraps (S/F crossings in the pose stream) to the
lap timer's exact S/F crossing times. Input records (arr_30/arr_48) have their own session clock,
which can be offset from the pose clock (0.6 s on the Spa fixtures); that offset is fitted per file by
matching path speed (from pose) against the decoded speed channel. Never assume it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

SYNC_MIN_R = 0.98  # whole-session match
LAP_SYNC_MIN_R = 0.95  # per lap: 26 Hz path speed is noisy; good laps measure 0.95-0.997


def spline_wraps(spline: np.ndarray) -> np.ndarray:
    """Pose indices of the first sample after each S/F crossing."""
    s = np.asarray(spline, dtype=np.float64)
    return np.nonzero((s[:-1] > 0.9) & (s[1:] < 0.1))[0] + 1


def _crossings(laps: Sequence[Dict[str, Any]]) -> np.ndarray:
    # Lap 0 starts when recording starts (not at S/F); later laps start at a crossing.
    return np.array(
        [float(l.get("sf_time_s", l["session_start_s"])) for l in list(laps)[1:]],
        dtype=np.float64,
    )


def _estimate_rate(gw: np.ndarray, gc: np.ndarray) -> Optional[float]:
    """Pose samples per second: the ratio shared by many (pose gap, timer gap) pairs."""
    cand = []
    for g in gw:
        r = g / gc[gc > 20.0]
        cand.append(r[(r > 5.0) & (r < 120.0)])
    if not cand:
        return None
    allr = np.concatenate(cand)
    if not len(allr):
        return None
    bins = np.arange(5.0, 120.0, 0.05)
    h, e = np.histogram(allr, bins=bins)
    k = int(np.argmax(h))
    m = (allr >= e[max(k - 1, 0)]) & (allr < e[min(k + 2, len(e) - 1)])
    return float(np.median(allr[m])) if m.any() else None


def _match_gaps(gw: np.ndarray, gc: np.ndarray, hz: float, tol: float) -> list:
    """Monotone alignment of pose wrap gaps with timer lap gaps (DP, longest matching chain)."""
    n, m = len(gw), len(gc)
    ok = np.abs(gw[:, None] / np.maximum(gc[None, :], 1e-6) / hz - 1.0) < tol
    dp = np.zeros((n + 1, m + 1), dtype=np.int32)
    for a in range(n - 1, -1, -1):
        for b in range(m - 1, -1, -1):
            dp[a, b] = max(dp[a + 1, b], dp[a, b + 1], dp[a + 1, b + 1] + 1 if ok[a, b] else 0)
    pairs, a, b = [], 0, 0
    while a < n and b < m:
        if ok[a, b] and dp[a, b] == dp[a + 1, b + 1] + 1:
            pairs.append((a, b))
            a += 1
            b += 1
        elif dp[a + 1, b] >= dp[a, b + 1]:
            a += 1
        else:
            b += 1
    return pairs


def fit_pose_clock(
    spline: np.ndarray,
    laps: Sequence[Dict[str, Any]],
    *,
    span_s: Optional[Tuple[float, float]] = None,
    gap_tol: float = 0.015,
    fallback_on_inconsistent: bool = True,
) -> Dict[str, Any]:
    """Session time (s) for every pose frame.

    Each pose spline wrap is a pass of the track's spline origin; each timer lap starts at an
    exact S/F crossing. Lap *durations* are matched (pose samples ÷ seconds ≈ constant rate within
    a lap) with a monotone alignment, so pauses, pit stops and missing laps don't derail it.
    Matched wraps become anchors; time is piecewise-linear between them. Note the spline origin
    can sit metres away from the timing line — ``fit_lag_field`` absorbs that.
    """
    n = len(spline)
    wraps = spline_wraps(spline)
    sf = _crossings(laps)
    hz = None
    anchors: Dict[int, float] = {}
    if len(wraps) >= 2 and len(sf) >= 2:
        gw = np.diff(wraps).astype(np.float64)
        gc = np.diff(sf)
        hz = _estimate_rate(gw, gc)
        if hz:
            pairs = _match_gaps(gw, gc, hz, gap_tol)
            # Similar lap times make the alignment ambiguous by whole laps: try shifting the matched
            # timer laps and keep the variant whose pose timeline best covers the input timeline.
            best = None
            for d in range(-len(sf) + 1, len(sf)):
                cand = [(a, b + d) for a, b in pairs if 0 <= b + d < len(gc)
                        and abs(gw[a] / max(gc[b + d], 1e-6) / hz - 1.0) < gap_tol]
                if len(cand) < max(1, int(0.8 * len(pairs))):
                    continue
                anc = {}
                for a, b in cand:
                    anc[int(wraps[a])] = float(sf[b])
                    anc[int(wraps[a + 1])] = float(sf[b + 1])
                k0 = min(anc)
                t_first = anc[k0] - k0 / hz
                t_last = anc[max(anc)] + (n - 1 - max(anc)) / hz
                err = 0.0 if span_s is None else abs(t_first - span_s[0]) + abs(t_last - span_s[1])
                key = (err, -len(cand))
                if best is None or key < best[0]:
                    best = (key, anc)
            if best is not None:
                anchors = best[1]
    if len(anchors) < 2:
        if span_s is None:
            raise ValueError("cannot fit pose clock: too few matched S/F crossings and no span")
        t = np.linspace(float(span_s[0]), float(span_s[1]), n)
        return {"t": t, "hz": (n - 1) / max(span_s[1] - span_s[0], 1e-9), "anchors": [],
                "method": "span_fallback", "matched_laps": 0, "median_resid_s": None, "max_resid_s": None}
    ai = np.array(sorted(anchors), dtype=np.float64)
    at = np.array([anchors[int(k)] for k in ai])
    # keep anchors monotone in time
    keep = np.r_[True, np.diff(at) > 0]
    ai, at = ai[keep], at[keep]
    t = np.interp(np.arange(n), ai, at)
    lo, hi = int(ai[0]), int(ai[-1])
    t[:lo] = at[0] - (lo - np.arange(lo)) / hz
    t[hi + 1 :] = at[-1] + (np.arange(hi + 1, n) - hi) / hz
    local_hz = np.diff(ai) / np.maximum(np.diff(at), 1e-9)
    spread = float(100 * (local_hz.max() - local_hz.min()) / hz) if len(local_hz) else 0.0
    # Consistent = the clock covers the same session as the inputs. (Local rate spread is reported
    # but not used: an anchor interval that spans a recording pause legitimately has a low rate.)
    consistent = span_s is None or (abs(t[0] - span_s[0]) < 30.0 and abs(t[-1] - span_s[1]) < 30.0)
    if not consistent and span_s is not None and fallback_on_inconsistent:
        # Lap matching contradicts itself (e.g. many near-identical race laps): fall back to the
        # input span; fit_lag_field's coarse speed alignment then recovers the true timing.
        t = np.linspace(float(span_s[0]), float(span_s[1]), n)
        return {"t": t, "hz": (n - 1) / max(span_s[1] - span_s[0], 1e-9), "anchors": [],
                "method": "span_fallback_inconsistent_laps", "matched_laps": int(len(ai) - 1),
                "rate_spread_pct": spread, "median_resid_s": None, "max_resid_s": None}
    return {
        "t": t,
        "hz": float(hz),
        "consistent": bool(consistent),
        "anchors": list(zip(ai.astype(int).tolist(), at.tolist())),
        "method": "lap_gap_match",
        "matched_laps": int(len(ai) - 1),
        "rate_spread_pct": spread,
        "median_resid_s": None,
        "max_resid_s": None,
    }


def path_speed_ms(x: np.ndarray, z: np.ndarray, t: np.ndarray, smooth: int = 5) -> np.ndarray:
    """Horizontal path speed from positions (m/s), boxcar-smoothed."""
    ds = np.r_[0.0, np.hypot(np.diff(x), np.diff(z))]
    dt = np.r_[1.0, np.diff(t)]
    k = np.ones(smooth)
    return np.convolve(ds, k, "same") / np.maximum(np.convolve(dt, k, "same"), 1e-6)


def fit_input_offset(
    x: np.ndarray,
    z: np.ndarray,
    pose_t: np.ndarray,
    input_t: np.ndarray,
    speed_kmh: np.ndarray,
    *,
    search_s: Tuple[float, float] = (-1.5, 1.5),
    step_s: float = 0.01,
) -> Dict[str, Any]:
    """Lag (s) such that inputs at ``pose_t + lag`` line up with the pose frames.

    Score = median |path speed − decoded speed| over moving samples. ``sync_go`` requires the
    correlation at the best lag to reach ``SYNC_MIN_R``.
    """
    v = path_speed_ms(np.asarray(x), np.asarray(z), np.asarray(pose_t)) * 3.6
    input_t = np.asarray(input_t, dtype=np.float64)
    spd = np.asarray(speed_kmh, dtype=np.float64)
    inside = (pose_t > input_t[0] + abs(search_s[0])) & (pose_t < input_t[-1] - abs(search_s[1]))
    best = None
    for lag in np.arange(search_s[0], search_s[1] + 1e-9, step_s):
        vt = np.interp(pose_t + lag, input_t, spd)
        m = inside & (vt > 30) & (v > 5) & (v < 420)
        if m.sum() < 100:
            continue
        score = float(np.median(np.abs(v[m] - vt[m])))
        if best is None or score < best[0]:
            best = (score, float(lag), m)
    if best is None:
        return {"lag_s": 0.0, "r": None, "median_abs_dv_kmh": None, "n": 0, "sync_go": False}
    score, lag, m = best
    vt = np.interp(pose_t + lag, input_t, spd)
    r = float(np.corrcoef(v[m], vt[m])[0, 1])
    return {
        "lag_s": round(lag, 3),
        "r": r,
        "median_abs_dv_kmh": score,
        "n": int(m.sum()),
        "sync_go": bool(r >= SYNC_MIN_R),
    }


def _coarse_lags(v_pose, pose_t, it, spd, *, window_s=60.0, step_s=30.0, search_s=150.0, hz=10.0, min_r=0.8):
    """Per-window lag (s) from normalized cross-correlation of speed traces on a 10 Hz grid."""
    g_in = np.arange(it[0], it[-1], 1.0 / hz)
    s_in = np.interp(g_in, it, spd)
    centres, lags, rs = [], [], []
    half = int(window_s * hz / 2)
    reach = int(search_s * hz)
    for c in np.arange(pose_t[0] + window_s / 2, pose_t[-1] - window_s / 2, step_s):
        gw = c + (np.arange(-half, half) / hz)
        a = np.interp(gw, pose_t, v_pose)
        if np.std(a) < 3.0:
            continue  # standing still / cruising: no shape to match
        k0 = int(round((c - g_in[0]) * hz))
        lo, hi = max(0, k0 - half - reach), min(len(s_in), k0 + half + reach)
        b = s_in[lo:hi]
        if len(b) < 2 * half + 10:
            continue
        a0 = (a - a.mean()) / (a.std() + 1e-9)
        # sliding normalized correlation
        csum = np.cumsum(np.r_[0.0, b]); csum2 = np.cumsum(np.r_[0.0, b * b])
        n = len(a0)
        num = np.correlate(b, a0, mode="valid")
        mean = (csum[n:] - csum[:-n]) / n
        var = np.maximum((csum2[n:] - csum2[:-n]) / n - mean**2, 1e-9)
        r = num / (n * np.sqrt(var))
        k = int(np.argmax(r))
        if r[k] < min_r:
            continue
        start_t = g_in[lo + k]
        centres.append(c)
        lags.append(start_t - gw[0])
        rs.append(float(r[k]))
    return np.array(centres), np.array(lags), np.array(rs)


def fit_lag_field(x, z, pose_t, input_t, speed_kmh, **kw) -> Dict[str, Any]:
    """Best of a fine-only and a coarse+fine lag fit (judged by overall speed match r)."""
    a = _fit_lag_field(x, z, pose_t, input_t, speed_kmh, coarse=False, **kw)
    if a.get("r") is not None and a["r"] >= 0.985:
        return a
    b = _fit_lag_field(x, z, pose_t, input_t, speed_kmh, coarse=True, **kw)
    return b if (b.get("r") or -1) > (a.get("r") or -1) else a


def _fit_lag_field(
    x: np.ndarray,
    z: np.ndarray,
    pose_t: np.ndarray,
    input_t: np.ndarray,
    speed_kmh: np.ndarray,
    *,
    window_s: float = 30.0,
    step_s: float = 15.0,
    search_s: float = 1.5,
    grid_s: float = 0.02,
    min_r: float = 0.95,
    coarse: bool = True,
) -> Dict[str, Any]:
    """Time-varying lag so inputs at ``pose_t + lag(t)`` line up with each pose frame.

    Fitted in overlapping windows by matching path speed to decoded speed; windows below ``min_r``
    are ignored and the lag is interpolated between good windows (median-smoothed).
    """
    v = path_speed_ms(np.asarray(x), np.asarray(z), np.asarray(pose_t)) * 3.6
    it = np.asarray(input_t, dtype=np.float64)
    spd = np.asarray(speed_kmh, dtype=np.float64)
    # Stage 1 (coarse): whole-lap-scale offsets from speed-trace cross-correlation — resolves
    # lap-time ambiguity and recording pauses that lap matching alone cannot.
    base = np.zeros(len(pose_t))
    coarse_info = None
    if coarse:
        cc, cl, cr = _coarse_lags(v, np.asarray(pose_t), it, spd)
        if len(cc) >= 3:
            sm_c = np.array([np.median(cl[max(0, k - 2) : k + 3]) for k in range(len(cl))])
            base = np.interp(pose_t, cc, sm_c)
            coarse_info = {"windows": int(len(cc)), "range_s": [float(sm_c.min()), float(sm_c.max())],
                           "median_r": float(np.median(cr))}
    lags = np.arange(-search_s, search_s + 1e-9, grid_s)
    centres, vals, rs = [], [], []
    for w0 in np.arange(pose_t[0], pose_t[-1], step_s):
        m = (pose_t >= w0) & (pose_t < w0 + window_s)
        if m.sum() < 50:
            continue
        tv, vv = pose_t[m] + base[m], v[m]
        best = None
        for lag in lags:
            vt = np.interp(tv + lag, it, spd)
            mm = (vt > 30) & (vv > 5) & (vv < 420)
            if mm.sum() < 40 or np.std(vt[mm]) < 1.0:
                continue
            r = np.corrcoef(vv[mm], vt[mm])[0, 1]
            if best is None or r > best[0]:
                best = (r, lag)
        if best and best[0] >= min_r:
            centres.append(w0 + window_s / 2)
            vals.append(best[1] + float(np.median(base[m])))
            rs.append(best[0])
    if not centres:
        g = fit_input_offset(x, z, pose_t + base, it, spd)
        lag_t = base + g["lag_s"]
        return {"lag": lag_t, "method": "global", "windows": 0, "coarse": coarse_info,
                **{k: g[k] for k in ("r", "sync_go")}}
    vals = np.array(vals)
    if len(vals) >= 5:
        sm = np.array([np.median(vals[max(0, i - 2) : i + 3]) for i in range(len(vals))])
    else:
        sm = vals
    lag_t = np.interp(pose_t, np.array(centres), sm)
    vt = np.interp(pose_t + lag_t, it, spd)
    mm = (vt > 30) & (v > 5) & (v < 420)
    r = float(np.corrcoef(v[mm], vt[mm])[0, 1]) if mm.sum() > 100 else None
    return {
        "lag": lag_t,
        "method": "windowed",
        "windows": len(centres),
        "coarse": coarse_info,
        "lag_range_s": [float(sm.min()), float(sm.max())],
        "r": r,
        "median_abs_dv_kmh": float(np.median(np.abs(v[mm] - vt[mm]))) if mm.any() else None,
        "sync_go": bool(r is not None and r >= SYNC_MIN_R),
    }


def span_clock(n: int, span_s: Tuple[float, float]) -> Dict[str, Any]:
    t = np.linspace(float(span_s[0]), float(span_s[1]), n)
    return {"t": t, "hz": (n - 1) / max(span_s[1] - span_s[0], 1e-9), "anchors": [], "method": "span"}


def fit_frame_times(spline, x, z, laps, input_t, speed_kmh) -> Tuple[Dict[str, Any], np.ndarray, Dict[str, Any]]:
    """Pose clock + lag field, choosing the starting clock (lap-matched or span) by speed match."""
    span = (float(input_t[0]), float(input_t[-1]))
    cands = []
    try:
        cands.append(fit_pose_clock(spline, laps, span_s=span, fallback_on_inconsistent=False))
    except ValueError:
        pass
    if not cands or not cands[0].get("consistent", False):
        cands.append(span_clock(len(spline), span))
    best = None
    for clk in cands:
        lag = fit_lag_field(x, z, clk["t"], input_t, speed_kmh)
        score = lag.get("r") if lag.get("r") is not None else -1.0
        if best is None or score > best[0]:
            best = (score, clk, lag)
    _, clk, lag = best
    lag_arr = lag.pop("lag")
    lag["clock_candidates"] = [c["method"] for c in cands]
    return clk, lag_arr, lag
