"""ACC electronics: settings (levels) vs intervention (active).

SHM contract (live session — Motec/SHM logger already use this split):

| Dimension | Source | Examples |
|-----------|--------|----------|
| **Level / setting** | `graphics.TC`, `TCCut`, `ABS`, `EngineMap` | TC=7, ABS=2 (garage + HUD) |
| **Active / state** | `physics.tc`, `physics.abs`, `tcinAction`, `absInAction` | fires on exit/entry |

Replay HUD mirrors the same via `ECarDigitalDisplayType::TC_Level*` /
`ABS_Level*` (settings) and `TC_InAction` / `ABS_InAction` (state).

`.rpy` status (2026-11-19): neither level nor InAction packer lanes are
Motec-locked yet. Export:

- `VRE_TC_ACT` / `VRE_ABS_ACT` — wheel-speed proxies for **state**
- `VRE_TC_LEVEL` / `VRE_ABS_LEVEL` / `VRE_TCCUT` / `VRE_ENG_MAP` — constant
  series when levels are supplied (garage JSON, future file decode, or HUD)
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import numpy as np


def levels_from_mapping(elec: Optional[Mapping[str, Any]]) -> Dict[str, Optional[float]]:
    """Normalize garage / SHM electronics dict → TC1, TC2/TCCut, ABS, EngineMap."""
    if not elec:
        return {"tc1": None, "tc2": None, "abs": None, "engine_map": None}
    def _get(*keys: str) -> Optional[float]:
        for k in keys:
            if k in elec and elec[k] is not None:
                try:
                    return float(elec[k])
                except (TypeError, ValueError):
                    continue
        return None

    return {
        "tc1": _get("tc1", "TC1", "tC1", "TC", "tc"),
        "tc2": _get("tc2", "TC2", "tC2", "TCCut", "tc_cut"),
        "abs": _get("abs", "ABS", "Abs"),
        "engine_map": _get("engine_map", "EngineMap", "ecuMap", "ECU"),
    }


def level_channels(
    n: int,
    *,
    tc1: Optional[float] = None,
    tc2: Optional[float] = None,
    abs_level: Optional[float] = None,
    engine_map: Optional[float] = None,
) -> Dict[str, np.ndarray]:
    """Session-constant Motec series for electronics **settings** (may be empty)."""
    out: Dict[str, np.ndarray] = {}
    if tc1 is not None:
        out["VRE_TC_LEVEL"] = np.full(n, float(tc1), dtype=np.float32)
    if tc2 is not None:
        out["VRE_TCCUT"] = np.full(n, float(tc2), dtype=np.float32)
    if abs_level is not None:
        out["VRE_ABS_LEVEL"] = np.full(n, float(abs_level), dtype=np.float32)
    if engine_map is not None:
        out["VRE_ENG_MAP"] = np.full(n, float(engine_map), dtype=np.float32)
    return out


def electronics_notes() -> Dict[str, str]:
    return {
        "VRE_TC_LEVEL": "setting: graphics.TC / HUD TC_Level (file lane NO-GO)",
        "VRE_TCCUT": "setting: graphics.TCCut / HUD TCCut_Level (file lane NO-GO)",
        "VRE_ABS_LEVEL": "setting: graphics.ABS / HUD ABS_Level (file lane NO-GO)",
        "VRE_ENG_MAP": "setting: graphics.EngineMap (file lane NO-GO)",
        "VRE_TC_ACT": "state: physics.tcinAction / Motec TC; proxy until packer lock",
        "VRE_ABS_ACT": "state: physics.absInAction / Motec ABS; proxy until packer lock",
    }
