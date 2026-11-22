"""Car and track identity stored inside ACC replays (numeric ids), independent of filenames.

Track ids were verified against 100+ local replays whose filenames/session names name the track.
Car ids follow ACC's public car-model enum (broadcasting SDK); ``CAR_VERIFIED`` lists the ones
cross-checked against replay filenames. Unknown ids fall back to filename hints.
"""

from __future__ import annotations

from typing import Dict, Optional

# track_id_check -> ACC Content/Cache folder (also the fastlane.ai folder)
TRACKS: Dict[int, str] = {
    1: "brands_hatch",
    2: "spa",
    3: "monza",
    4: "misano",
    5: "paul_ricard",
    6: "silverstone",
    7: "hungaroring",
    8: "nurburgring",
    9: "barcelona",
    10: "zolder",
    11: "zandvoort",
    12: "kyalami",
    13: "mount_panorama",
    14: "laguna_seca",
    15: "suzuka",
    16: "snetterton",
    17: "oulton_park",
    19: "imola",
    21: "cota",
    23: "watkins_glen",
    24: "valencia",
}

# player_car_model -> ACC car folder / setup folder name
CAR_MODELS: Dict[int, str] = {
    0: "porsche_991_gt3_r",
    1: "mercedes_amg_gt3",
    2: "ferrari_488_gt3",
    3: "audi_r8_lms",
    4: "lamborghini_huracan_gt3",
    5: "mclaren_650s_gt3",
    6: "nissan_gt_r_gt3_2018",
    7: "bmw_m6_gt3",
    8: "bentley_continental_gt3_2018",
    10: "nissan_gt_r_gt3_2017",
    11: "bentley_continental_gt3_2016",
    12: "amr_v12_vantage_gt3",
    13: "lamborghini_gallardo_rex",
    14: "jaguar_g3",
    15: "lexus_rc_f_gt3",
    16: "lamborghini_huracan_gt3_evo",
    17: "honda_nsx_gt3",
    19: "audi_r8_lms_evo",
    20: "amr_v8_vantage_gt3",
    21: "honda_nsx_gt3_evo",
    22: "mclaren_720s_gt3",
    23: "porsche_991ii_gt3_r",
    24: "ferrari_488_gt3_evo",
    25: "mercedes_amg_gt3_evo",
    30: "bmw_m4_gt3",
    31: "audi_r8_lms_evo_ii",
    32: "ferrari_296_gt3",
    33: "lamborghini_huracan_gt3_evo2",
    34: "porsche_992_gt3_r",
    35: "mclaren_720s_gt3_evo",
    36: "ford_mustang_gt3",
}
CAR_VERIFIED = {8, 12, 20, 22, 24, 31, 32, 35}


def track_folder(track_id: Optional[int]) -> Optional[str]:
    return TRACKS.get(int(track_id)) if track_id is not None else None


def car_folder(car_model: Optional[int]) -> Optional[str]:
    return CAR_MODELS.get(int(car_model)) if car_model is not None else None


def identity(ex, filename_hints: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, object]:
    """Best car/track identity: replay ids first, filename hints as fallback."""
    hints = filename_hints or {}
    track = track_folder(getattr(ex, "track_id_check", None))
    car = car_folder(getattr(ex, "player_car_model", None))
    return {
        "track": track or (hints.get("track") or None),
        "track_source": "replay_id" if track else "filename",
        "track_id": getattr(ex, "track_id_check", None),
        "car": car or (hints.get("car") or None),
        "car_source": "replay_id" if car else "filename",
        "car_model_id": getattr(ex, "player_car_model", None),
        "car_id_verified": getattr(ex, "player_car_model", None) in CAR_VERIFIED,
    }
