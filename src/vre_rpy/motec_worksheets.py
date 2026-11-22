"""ACC Motec worksheet → VRE channel → pace-first lever map.

Canonical prose: docs/superpowers/specs/acc-motec-pace-setup.md
Sonoma transfer + Kunos base_ACC worksheets + gt3_physics_kb doctrine.
"""

from __future__ import annotations

from typing import Any, Dict, List


DOCTRINE = {
    "goal": "repeatable_lap_time",
    "stability": "constraint_only_if_helps_or_neutral_to_pace",
    "reject": "more_stable_but_slower",
    "sonoma_transfer": [
        "controls_before_setup",
        "coast_B_and_C_dominate_over_A_stability_saves",
        "fast_passes_may_include_rear_slide",
        "gearing_is_a_pace_lever",
        "one_lever_ABA_median_flying",
    ],
    "acc_lever_order": [
        "tyres_window",
        "springs_arb",
        "aero_rake_wing",
        "diff",
        "brake_bias_abs",
        "dampers",
        "electronics_maps",
    ],
}


def worksheet_map() -> List[Dict[str, Any]]:
    """Machine-readable Motec worksheet map for agent packs / Motec coaching."""
    return [
        {
            "worksheet": "Compare",
            "channels": ["SPEED", "TIME", "LAP_DIST", "VRE_SPEED_KMH"],
            "lever_class": "driving_first",
            "pace_note": "Find where time is lost vs ref before touching setup.",
        },
        {
            "worksheet": "Driver",
            "channels": [
                "THROTTLE",
                "BRAKE",
                "STEERANGLE",
                "G_LAT",
                "G_LON",
                "VRE_COAST",
                "VRE_PEDAL_GAP",
                "VRE_STEER_RATE",
            ],
            "lever_class": "technique_then_setup",
            "pace_note": "Pedal gaps / coast B+C are not fixed by soft ARB or more TC.",
        },
        {
            "worksheet": "Wheelspeed",
            "channels": [
                "WHEEL_SPEED_LF",
                "WHEEL_SPEED_RF",
                "WHEEL_SPEED_LR",
                "WHEEL_SPEED_RR",
                "SPEED",
                "VRE_SPIN",
                "VRE_LOCK",
                "VRE_TC_EST",
                "VRE_ABS_EST",
                "VRE_TC_LEVEL",
                "VRE_ABS_LEVEL",
            ],
            "lever_class": "diff_bias_electronics",
            "pace_note": (
                "TC/ABS LEVELS are settings; ACT is state. Raise maps only if "
                "intervention costs lap time vs spin/lock cost (Sonoma: don't calm)."
            ),
        },
        {
            "worksheet": "Understeer_Oversteer",
            "channels": ["VRE_US_OS", "STEERANGLE", "G_LAT", "SPEED", "VRE_YAW_DEG"],
            "lever_class": "springs_arb_aero_diff",
            "pace_note": "Multi-corner pattern only; one corner = driving.",
        },
        {
            "worksheet": "Suspension_Travel",
            "channels": [
                "SUS_TRAVEL_LF",
                "SUS_TRAVEL_RF",
                "SUS_TRAVEL_LR",
                "SUS_TRAVEL_RR",
                "VRE_RAKE_LOAD",
                "VRE_LOAD_F",
                "VRE_LOAD_R",
            ],
            "lever_class": "ride_height_bumpstop_springs",
            "pace_note": "rpy uses tyre_load F−R proxy until SUS_TRAVEL Motec-locks.",
        },
        {
            "worksheet": "Suspension_Histogram",
            "channels": ["VRE_DV_LF", "VRE_DV_RF", "VRE_DV_LR", "VRE_DV_RR"],
            "lever_class": "dampers_last",
            "pace_note": "Bell shape after springs/ARB sane; ACC 1.10+ four-way.",
        },
        {
            "worksheet": "Engine_Revs",
            "channels": ["RPMS", "GEAR"],
            "lever_class": "gearing_pace",
            "pace_note": "Sonoma: shorter gear was a proven pace lever, not stability.",
        },
        {
            "worksheet": "Tyres",
            "channels": [
                "TYRE_PRESS_LF",
                "TYRE_PRESS_RF",
                "TYRE_PRESS_LR",
                "TYRE_PRESS_RR",
                "TYRE_TAIR_LF",
                "TYRE_TAIR_RF",
                "TYRE_TAIR_LR",
                "TYRE_TAIR_RR",
                "BRAKE_TEMP_LF",
                "BRAKE_TEMP_RF",
                "BRAKE_TEMP_LR",
                "BRAKE_TEMP_RR",
                "VRE_BT_BAL",
                "VRE_TRACK_GRIP",
                "VRE_LAT_UTIL",
            ],
            "lever_class": "tyres_first",
            "pace_note": "Dry hot 26–27 psi; core 70–100 °C. Pre-1.9 ~28 psi dogma rejected.",
        },
    ]


def pack_json() -> Dict[str, Any]:
    return {
        "schema": "vre.acc_motec_worksheets.v1",
        "spec": "docs/superpowers/specs/acc-motec-pace-setup.md",
        "doctrine": DOCTRINE,
        "worksheets": worksheet_map(),
        "launcher": {
            "module": "vre_rpy.motec_workspace",
            "bat": "motec/Open_VRE.bat",
            "active_workbook": "VRE Compare",
            "note": (
                "export_rpy_analysis writes Motec Workspace.i2wsp with Workbook XRefs "
                "(required — empty Workbooks = blank Layouts UI) and Open_VRE.bat."
            ),
        },
    }
