"""Full-rate per-lap replay frames for frame-by-frame review (any car, any track).

One frame per pose record (car position, 14-27 Hz depending on replay quality). Inputs (8-11 Hz)
are interpolated onto each frame through the fitted input-clock offset (``frame_clock``); gear is
step-held. Distance is measured along the track's AI line when the installed ``fastlane.ai`` is
available, else from the replay spline × track length.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import numpy as np

from .arr30_decode import CORNERS, decode_arr30, find_arr, tyre_radii
from .frame_clock import LAP_SYNC_MIN_R, fit_frame_times, path_speed_ms
from .ids import identity
from .lap_validity import decode_lap_validity, decode_predicted_lap, find_lap_timer_array
from .parse_rpy import extract_filename_hints
from .pose_track import decode_pose_track, find_pose, load_track_config
from .processor import extract_rpy, list_cars, player_car_index
from .track_projection import TrackLine, load_track_line

SCHEMA = "vre.acc_rpy.lap_frames.v1"
BRAKE_BLIP_MASK = 0.30  # throttle while braking harder than this = ACC auto-blip, not the driver


def _wrap_deg(a: np.ndarray) -> np.ndarray:
    return (a + 180.0) % 360.0 - 180.0


class ReplayFrames:
    """Decoded replay + fitted clocks; produces per-lap frame tables."""

    def __init__(self, path: Union[str, Path], *, car_index: Optional[int] = None, cache_dir=None):
        self.path = Path(path)
        ex = extract_rpy(self.path)
        self.ident = identity(ex, extract_filename_hints(self.path.name))
        cfg = load_track_config(str(self.ident.get("track") or ""))
        self.track_config = cfg
        pose = find_pose(ex, car_index)
        if pose is None:
            raise ValueError("replay has no pose stream for this car")
        self.pose = decode_pose_track(
            pose, track_length_m=float(cfg.get("length_m") or 7000.0), corners=cfg.get("corners")
        )
        a30, a48 = find_arr(ex, 0x30, car_index), find_arr(ex, 0x48, car_index)
        if a30 is None:
            raise ValueError("replay has no input stream for this car")
        self.car_index = player_car_index(ex) if car_index is None else int(car_index)
        self.car = next((c for c in list_cars(ex) if c["car_index"] == self.car_index), {})
        if not self.car.get("is_player", True):
            # the replay's car-model id is the recording driver's; an opponent's model is not known
            self.ident = {**self.ident, "car": None, "car_source": "unknown_opponent", "car_model_id": None}
        self.inputs = decode_arr30(a30, a48, wheel_radius_m=tyre_radii(ex, car_index))
        arr24 = find_lap_timer_array(ex, car_index)
        self.laps: List[Dict[str, Any]] = decode_lap_validity(arr24.data) if arr24 is not None else []
        self.predicted = decode_predicted_lap(arr24.data) if arr24 is not None else None
        it = self.inputs["time_s"]
        self.clock, self.lag, self.sync = fit_frame_times(
            self.pose["spline"], self.pose["x"], self.pose["z"], self.laps, it, self.inputs["speed_kmh"]
        )
        # true session time of each pose frame (pose clock + spline-origin / clock offset)
        self.t = self.clock["t"] + self.lag
        self.line: Optional[TrackLine] = (
            load_track_line(str(self.ident["track"]), cache_dir=cache_dir) if self.ident.get("track") else None
        )
        self.length_m = self.line.length_m if self.line else float(cfg.get("length_m") or 7000.0)

    # ------------------------------------------------------------------ helpers
    def _lap_window(self, i: int):
        lap = self.laps[i]
        t0 = lap.get("sf_time_s", lap["session_start_s"])
        if i + 1 < len(self.laps):
            nxt = self.laps[i + 1]
            t1 = nxt.get("sf_time_s", nxt["session_start_s"])
        else:
            t1 = lap["session_end_s"]
        return float(t0), float(t1)

    def _inp(self, key, tq, *, corner=None, step=False):
        src = self.inputs[key] if corner is None else self.inputs[key][corner]
        it = self.inputs["time_s"]
        if step:
            k = np.clip(np.searchsorted(it, tq, side="right") - 1, 0, len(it) - 1)
            return np.asarray(src)[k]
        return np.interp(tq, it, np.asarray(src, dtype=np.float64))

    def best_lap_index(self) -> Optional[int]:
        good = [(l["lap_ms"], l["lap_index"]) for l in self.laps if l.get("flying_candidate") and l.get("valid")]
        return min(good)[1] if good else None

    # ------------------------------------------------------------------ frames
    def lap_frames(self, lap_index: int) -> Dict[str, Any]:
        lap = self.laps[lap_index]
        t0, t1 = self._lap_window(lap_index)
        idx = np.nonzero((self.t >= t0) & (self.t < t1))[0]
        if len(idx) < 10:
            raise ValueError(f"lap {lap_index}: no pose frames in its time window")
        p = self.pose
        tq = self.t[idx]
        xz = np.c_[p["x"][idx], p["z"][idx]]
        cols: Dict[str, Any] = {"t": self.t[idx] - t0}

        if self.line is not None:
            pr = self.line.project(xz)
            s = pr["s_m"].copy()
            L = self.line.length_m
            n = len(s)
            first, last = np.arange(n) < n * 0.15, np.arange(n) > n * 0.85
            s[first & (s > L * 0.5)] -= L
            s[last & (s < L * 0.5)] += L
            cols["s_m"] = s
            cols["lat_m"] = pr["lat_m"]
            beyond_l, beyond_r = [], []
            for w in range(4):
                b = self.line.beyond_edge(p["wheels"][idx, w][:, [0, 2]])
                beyond_l.append(b["left"])
                beyond_r.append(b["right"])
            bl, br = np.array(beyond_l), np.array(beyond_r)
            cols["edge_all4_left_m"] = bl.min(0)   # > 0: all four wheels beyond the left edge
            cols["edge_all4_right_m"] = br.min(0)
            cols["edge_any_left_m"] = bl.max(0)    # > 0: at least one wheel beyond the left edge
            cols["edge_any_right_m"] = br.max(0)
            distance_source = "ai_line"
        else:
            sp = p["spline"][idx].copy()
            n = len(sp)
            sp[(np.arange(n) < n * 0.15) & (sp > 0.5)] -= 1.0
            sp[(np.arange(n) > n * 0.85) & (sp < 0.5)] += 1.0
            cols["s_m"] = sp * self.length_m
            distance_source = "spline"

        cols["x"], cols["y"], cols["z"] = p["x"][idx], p["y"][idx], p["z"][idx]
        # Wheel footprint in the body frame (fwd, left) — near-constant per lap, so stored once in meta;
        # per-frame footprint = centre + rotate(offsets, yaw). Edge margins above use the real points.
        yaw = np.radians(p["yaw_deg"][idx])
        fx, fz = np.sin(yaw), np.cos(yaw)
        wheel_body = []
        for w in range(4):
            dx = p["wheels"][idx, w, 0] - p["x"][idx]
            dz = p["wheels"][idx, w, 2] - p["z"][idx]
            wheel_body.append([float(np.median(dx * fx + dz * fz)), float(np.median(dx * fz - dz * fx))])
        cols["yaw_deg"] = p["yaw_deg"][idx]
        v_path = path_speed_ms(p["x"][idx], p["z"][idx], self.t[idx]) * 3.6
        v_in = self._inp("speed_kmh", tq)
        mv = (v_in > 30) & (v_path > 5) & (v_path < 420)
        lap_r = float(np.corrcoef(v_path[mv], v_in[mv])[0, 1]) if mv.sum() > 50 else None
        lap_sync_go = bool(lap_r is not None and lap_r >= LAP_SYNC_MIN_R)
        if lap_sync_go:
            vx, vz = self._inp("vel_x", tq), self._inp("vel_z", tq)
            course = np.degrees(np.arctan2(vx, vz))
            course_src = "velocity"
        else:
            course = p["heading_deg"][idx]
            course_src = "pose_motion"
        cols["course_deg"] = course
        cols["slip_deg"] = _wrap_deg(p["yaw_deg"][idx] - course)

        for key in ("speed_kmh", "throttle", "brake", "steer_deg", "rpm", "g_lat", "g_lon", "g_vert"):
            cols[key] = self._inp(key, tq)
        cols["gear"] = self._inp("gear", tq, step=True)
        cols["throttle_masked"] = np.where(cols["brake"] > BRAKE_BLIP_MASK, 0.0, cols["throttle"])
        for grp in ("tyre_load", "slip_ratio"):
            cols[grp] = {c: self._inp(grp, tq, corner=c) for c in CORNERS if c in self.inputs.get(grp, {})}
        if self.predicted is not None:
            pt, pm = self.predicted["time_s"], self.predicted["predicted_ms"]
            k = np.clip(np.searchsorted(pt, self.t[idx], side="right") - 1, 0, len(pt) - 1)
            cols["predicted_lap_ms"] = pm[k]
        inv = lap.get("invalid_at_session_s")
        cols["lap_valid_so_far"] = (self.t[idx] < inv).astype(float) if inv else np.ones(len(idx))

        meta = {
            "schema": SCHEMA,
            "lap_index": lap_index,
            "lap_ms": lap["lap_ms"],
            "completed": lap.get("completed"),
            "flying": lap.get("flying_candidate"),
            "valid": lap.get("valid"),
            "invalid_at_lap_ms": lap.get("invalid_at_lap_ms"),
            "frames": int(len(idx)),
            # recording started after this lap's S/F crossing (or ended early): not a full lap
            "partial": bool(cols["t"][0] > 0.5 or cols["t"][-1] < lap["lap_ms"] / 1000.0 - 1.0),
            "pose_hz": round(float(self.clock["hz"]), 3),
            "input_hz": round(float(len(self.inputs["time_s"]) / max(self.inputs["time_s"][-1] - self.inputs["time_s"][0], 1e-9)), 3),
            "clock": {k: v for k, v in self.clock.items() if k not in ("t", "anchors")},
            "sync": {**self.sync, "lap_r": lap_r, "lap_sync_go": lap_sync_go},
            "distance_source": distance_source,
            "track_length_m": round(self.length_m, 1),
            "limits_official": bool(getattr(self.line, "limits_official", False)) if self.line else False,
            "course_source": course_src,
            "wheel_radius_m": self.inputs.get("wheel_radius_m"),
            "wheel_body_m": [[round(a, 3), round(b, 3)] for a, b in wheel_body],
            "identity": self.ident,
            "car": {k: self.car.get(k) for k in ("car_index", "car_key", "drivers", "is_player", "has_wheel_data")},
            "notes": {
                "throttle_masked": f"throttle zeroed while brake > {BRAKE_BLIP_MASK:.0%} (ACC auto-blip)",
                "edge_*": "metres beyond the official AI-line edge; fastlane edges are not the white lines everywhere",
                "inputs": "interpolated from native input rate; gear step-held",
                "wheel_body_m": "per-lap median wheel offsets [forward, side] from the centre, in the car frame (yaw_deg)",
            },
        }
        return {"meta": meta, **_round_cols(cols)}

    def summary(self) -> Dict[str, Any]:
        return {
            "file": self.path.name,
            "identity": self.ident,
            "car": self.car,
            "pose_hz": self.clock["hz"],
            "clock_method": self.clock["method"],
            "sync": self.sync,
            "track_length_m": self.length_m,
            "distance_source": "ai_line" if self.line else "spline",
            "best_lap_index": self.best_lap_index(),
            "laps": [
                {k: l.get(k) for k in ("lap_index", "lap_ms", "completed", "flying_candidate", "valid", "invalid_at_lap_ms")}
                for l in self.laps
            ],
        }


_ROUND = {
    "t": 3, "s_m": 1, "lat_m": 2, "x": 2, "y": 2, "z": 2, "yaw_deg": 2, "course_deg": 2, "slip_deg": 2,
    "speed_kmh": 2, "throttle": 3, "brake": 3, "throttle_masked": 3, "steer_deg": 1, "rpm": 0,
    "g_lat": 3, "g_lon": 3, "g_vert": 3, "gear": 0, "predicted_lap_ms": 0, "lap_valid_so_far": 0,
}


def _r(a, nd):
    arr = np.asarray(a, dtype=np.float64)
    out = np.round(arr, nd)
    return [None if not np.isfinite(v) else (int(v) if nd == 0 else float(v)) for v in out]


def _round_cols(cols: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in cols.items():
        if isinstance(v, dict):
            out[k] = {c: _r(arr, 3 if k == "slip_ratio" else 0) for c, arr in v.items()}
        else:
            out[k] = _r(v, _ROUND.get(k, 2))
    return out


def _select_laps(rf: ReplayFrames, laps: Union[str, Sequence[int]]) -> List[int]:
    if laps == "all":
        return [l["lap_index"] for l in rf.laps if l.get("completed")]
    if laps == "best":
        b = rf.best_lap_index()
        return [b] if b is not None else []
    return [int(x) for x in laps]


def write_frames(
    specs: Iterable[tuple],
    out_dir: Union[str, Path],
    *,
    laps: Union[str, Sequence[int]] = "all",
    cache_dir=None,
) -> Dict[str, Any]:
    """Write ``<out>/<label>/lap_NN.json`` + ``<out>/index.json`` for each (rpy_path, label)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index: Dict[str, Any] = {"schema": SCHEMA, "drivers": []}
    for rpy, label in specs:
        rf = ReplayFrames(rpy, cache_dir=cache_dir)
        d = out / label
        d.mkdir(exist_ok=True)
        written = []
        for li in _select_laps(rf, laps):
            try:
                fr = rf.lap_frames(li)
            except ValueError as exc:
                written.append({"lap_index": li, "error": str(exc)})
                continue
            fn = d / f"lap_{li:02d}.json"
            fn.write_text(json.dumps(fr, separators=(",", ":")), encoding="utf-8")
            written.append({"lap_index": li, "file": f"{label}/{fn.name}", "lap_ms": fr["meta"]["lap_ms"],
                            "valid": fr["meta"]["valid"], "frames": fr["meta"]["frames"]})
        index["drivers"].append({"label": label, **rf.summary(), "written": written})
    (out / "index.json").write_text(json.dumps(index, indent=2, default=float), encoding="utf-8")
    return index
