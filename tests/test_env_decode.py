"""env_decode: weatherData floats stay raw (named fields are hypotheses)."""

from __future__ import annotations

import pytest

from _fixtures import TOMATO_SPA, need_fixture
from vre_rpy.env_decode import decode_weather_data, env_summary_from_extract


def test_decode_weather_data_raw_go():
    raw = [30.2, 0.25, 0.0, 37.5, 0.0, 0.0, 0.66]
    out = decode_weather_data(raw)
    assert out["raw"] == raw
    assert out["named"]["RPY_WEATHER_0"] == 30.2
    assert out["field_status"]["RPY_WEATHER_0"] == "GO_RAW"
    assert out["field_status"]["air_temp_c"] == "hypothesis"
    assert out["go_for_coaching"] is False


def test_env_summary_from_spa_extract():
    from vre_rpy.processor import extract_rpy, extract_to_dict

    ex = extract_to_dict(extract_rpy(need_fixture(TOMATO_SPA)))
    env = env_summary_from_extract(ex)
    assert len(env["weather_data"]["raw"]) == 7
