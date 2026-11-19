"""Decode .rpy weather blobs into named env fields.

Status: raw weatherData[7] is GO as floats; SHM-like *names* are hypotheses
until Motec/SHM cross-check locks them. Never cite hypothesis names as GO
setup/grip facts — expose both raw and tentative labels.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


# Tentative index map (Spa gold weather_data ≈ ambient 30°C, road 37°C, grip ~0.67).
# Documented as hypothesis — not Motec-locked.
_HYP_INDEX = {
    0: ("air_temp_c", "hypothesis"),
    1: ("cloud", "hypothesis"),
    2: ("rain_intensity_norm", "hypothesis"),
    3: ("road_temp_c", "hypothesis"),
    4: ("wind_speed", "hypothesis"),
    5: ("wind_dir", "hypothesis"),
    6: ("track_grip_norm", "hypothesis"),
}


def decode_weather_data(weather_data: Sequence[float]) -> dict[str, Any]:
    """Map processor weather_data (7×f32) to raw + tentative named fields."""
    raw = [float(x) for x in weather_data]
    named: dict[str, Any] = {}
    status: dict[str, str] = {}
    for i, val in enumerate(raw):
        key = f"RPY_WEATHER_{i}"
        named[key] = val
        status[key] = "GO_RAW"
        if i in _HYP_INDEX:
            hyp_name, st = _HYP_INDEX[i]
            named[hyp_name] = val
            status[hyp_name] = st
    return {
        "raw": raw,
        "named": named,
        "field_status": status,
        "go_for_coaching": False,  # names not Motec-locked yet
        "note": (
            "Use RPY_WEATHER_* as GO raw floats only. "
            "air_temp_c / road_temp_c / track_grip_norm are hypotheses."
        ),
    }


def decode_weather_status(weather_status: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pass through weatherStatus entries without inventing meanings."""
    entries = []
    for ent in weather_status:
        entries.append(
            {
                "names": list(ent.get("names") or []),
                "fields": dict(ent.get("fields") or {}),
                "id": ent.get("id"),
                "status": "OPAQUE_PASS_THROUGH",
            }
        )
    return {
        "entries": entries,
        "go_for_coaching": False,
        "note": "weatherStatus field meanings not Motec-locked; pass-through only.",
    }


def env_summary_from_extract(extract: Mapping[str, Any]) -> dict[str, Any]:
    """Build env block for coaching evidence pack from processor extract dict."""
    wd = decode_weather_data(extract.get("weather_data") or [])
    ws = decode_weather_status(extract.get("weather_status") or [])
    return {
        "weather_data": wd,
        "weather_status": ws,
        "evidence": "PARTIAL",  # raw GO, names NO_GO
    }
