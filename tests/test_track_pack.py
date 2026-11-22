"""Generated track packs: detection on synthetic geometry + shipped packs sanity."""

from __future__ import annotations

import json

import numpy as np
import pytest

from vre_rpy.track_pack_gen import GEN_DIR, attach_notes, generate, load_track_pack


def _rounded_square(side=400.0, r=40.0, step=2.0, bump_at=None):
    """Closed path turning the same way at 4 corners (theta increasing = right turns in x,z)."""
    pts, theta = [], 0.0
    x, z = 0.0, 0.0
    for _ in range(4):
        for _ in range(int(side / step)):
            x += step * np.cos(theta); z += step * np.sin(theta); pts.append((x, z))
        for _ in range(int((np.pi / 2) * r / step)):
            theta += step / r
            x += step * np.cos(theta); z += step * np.sin(theta); pts.append((x, z))
    pts = np.array(pts)
    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    y = np.zeros(len(pts))
    if bump_at is not None:
        y = 3.0 * np.exp(-((s - bump_at) / 25.0) ** 2) - 3.0 * np.exp(-((s - bump_at - 200) / 25.0) ** 2)
    return {"centerline_xyz": np.c_[pts[:, 0], y, pts[:, 1]].tolist()}


def test_detects_four_right_corners_and_terrain():
    pack = generate(_rounded_square(bump_at=150.0), track_id="synthetic")
    assert len(pack["corners"]) == 4
    assert all(c["direction"] == "right" for c in pack["corners"])
    assert all(c["speed_class"] == "slow" for c in pack["corners"])
    assert all(abs(c["min_radius_m"] - 40) < 12 for c in pack["corners"])
    crest = [t for t in pack["terrain"] if t["kind"] == "crest" and abs(t["peak_m"] - 150) < 20]
    dip = [t for t in pack["terrain"] if t["kind"] == "dip" and abs(t["peak_m"] - 350) < 20]
    assert crest and dip
    assert crest[0]["dg_at_100kmh"] < 0 < dip[0]["dg_at_100kmh"]  # crest unloads, dip loads


def test_mirrored_track_turns_left():
    g = _rounded_square()
    g["centerline_xyz"] = [[x, y, -z] for x, y, z in g["centerline_xyz"]]
    pack = generate(g, track_id="mirror")
    assert all(c["direction"] == "left" for c in pack["corners"])


def test_notes_only_relabel():
    pack = generate(_rounded_square(), track_id="synthetic")
    before = [(c["apex_m"], c["direction"]) for c in pack["corners"]]
    notes = {"corners": [{"name": "Hairpin A", "apex_m_range": [0, pack["corners"][0]["apex_m"] + 1], "cues": ["x"]}]}
    out = attach_notes(json.loads(json.dumps(pack)), notes)
    assert out["corners"][0]["display_name"] == "Hairpin A"
    assert [(c["apex_m"], c["direction"]) for c in out["corners"]] == before


def test_shipped_packs_are_sane():
    files = sorted(GEN_DIR.glob("*.json"))
    if not files:
        pytest.skip("no generated packs shipped")
    for f in files:
        p = json.loads(f.read_text())
        assert p["length_m"] > 2000 and len(p["corners"]) >= 5, f.name
        sp = [c["apex_spline"] for c in p["corners"]]
        assert sp == sorted(sp) and all(0 <= v < 1 for v in sp), f.name
    spa = load_track_pack("spa")
    if spa is not None:
        ls = spa["corners"][0]
        assert ls["display_name"] == "La Source" and ls["direction"] == "right" and ls["min_radius_m"] < 40
        assert 6800 < spa["length_m"] < 7100
    rbr = load_track_pack("red_bull_ring")
    if rbr is not None:
        assert abs(rbr["length_m"] - 4318) < 100 and rbr["elevation_range_m"] < 100
