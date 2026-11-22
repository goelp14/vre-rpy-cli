"""Project world positions onto a track's AI line (fastlane.ai) — any track.

Gives distance along the line, signed lateral offset (+ toward the left-limit side) and the
official half-widths at that point, so wheel positions can be compared with the track edges.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np


class TrackLine:
    def __init__(self, centerline, left_limit, right_limit, *, closed: bool = True):
        self.c = np.asarray(centerline, dtype=np.float64)
        self.left = np.asarray(left_limit, dtype=np.float64)
        self.right = np.asarray(right_limit, dtype=np.float64)
        self.n = len(self.c)
        self.closed = closed
        b = np.roll(self.c, -1, axis=0)
        self.ab = b - self.c
        self.l2 = np.maximum((self.ab**2).sum(1), 1e-9)
        self.seglen = np.sqrt(self.l2)
        if not closed:
            self.seglen[-1] = 0.0
        self.cum = np.r_[0.0, np.cumsum(self.seglen)[:-1]]
        self.length_m = float(self.seglen.sum())
        nl = self.left - self.c
        self.half_left = np.linalg.norm(nl, axis=1)
        self.n_left = nl / np.maximum(self.half_left[:, None], 1e-9)
        self.half_right = np.linalg.norm(self.right - self.c, axis=1)

    @classmethod
    def from_fastlane(cls, geo: Dict[str, Any]) -> "TrackLine":
        return cls(geo["centerline"], geo["left_limit"], geo["right_limit"])

    def project(self, xz: np.ndarray, chunk: int = 4000) -> Dict[str, np.ndarray]:
        """xz (n, 2) world X/Z → dict of s_m, lat_m, half_left_m, half_right_m, seg."""
        p_all = np.asarray(xz, dtype=np.float64)
        n = len(p_all)
        out = {k: np.empty(n) for k in ("s_m", "lat_m", "half_left_m", "half_right_m")}
        seg_out = np.empty(n, dtype=np.int64)
        for c0 in range(0, n, chunk):
            p = p_all[c0 : c0 + chunk]
            d = ((p[:, None, :] - self.c[None]) ** 2).sum(2)
            i = np.argmin(d, axis=1)
            best_d = np.full(len(p), np.inf)
            best_j = i.copy()
            best_t = np.zeros(len(p))
            for j in ((i - 1) % self.n, i):
                t = np.clip(((p - self.c[j]) * self.ab[j]).sum(1) / self.l2[j], 0.0, 1.0)
                q = self.c[j] + t[:, None] * self.ab[j]
                dd = ((p - q) ** 2).sum(1)
                m = dd < best_d
                best_d = np.where(m, dd, best_d)
                best_j = np.where(m, j, best_j)
                best_t = np.where(m, t, best_t)
            q = self.c[best_j] + best_t[:, None] * self.ab[best_j]
            sl = slice(c0, c0 + len(p))
            out["s_m"][sl] = self.cum[best_j] + best_t * self.seglen[best_j]
            out["lat_m"][sl] = ((p - q) * self.n_left[best_j]).sum(1)
            out["half_left_m"][sl] = self.half_left[best_j]
            out["half_right_m"][sl] = self.half_right[best_j]
            seg_out[sl] = best_j
        out["seg"] = seg_out
        return out

    def beyond_edge(self, xz: np.ndarray) -> Dict[str, np.ndarray]:
        """Metres beyond the left / right edge (positive = outside that edge)."""
        pr = self.project(xz)
        return {
            "left": pr["lat_m"] - pr["half_left_m"],
            "right": -pr["lat_m"] - pr["half_right_m"],
            "s_m": pr["s_m"],
            "lat_m": pr["lat_m"],
        }


def load_track_line(track_id: str, cache_dir=None) -> Optional[TrackLine]:
    """TrackLine from the installed ACC fastlane.ai, or None if it can't be found/parsed."""
    from .acc_fastlane import parse_acc_fastlane, resolve_fastlane

    try:
        geo = parse_acc_fastlane(resolve_fastlane(track_id, cache_dir=cache_dir))
    except (FileNotFoundError, ValueError):
        return None
    line = TrackLine.from_fastlane(geo)
    line.limits_official = bool(geo.get("limits_official"))  # type: ignore[attr-defined]
    return line
