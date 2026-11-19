"""Labeled stream catalog from Ghidra decomp-first RE.

Runtime offsets are GO for process memory. File lanes unlock via arr_30/arr_48
packing (FUN_140fdf850) — see arr30_decode.py.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Status:
#   runtime_go  — float/int source proven in AC2-Win64-Shipping.exe
#   file_go     — decoded from .rpy and Motec-locked (or lap-timer gold)
#   file_hyp    — present as bytes/arrays; meaning not Motec-locked
#   no_go       — not extractable yet

_CORNERS = ("LF", "RF", "LR", "RR")


def _corner_streams(
    id_prefix: str,
    label: str,
    unit: str,
    source: str,
    *,
    evidence: str,
    runtime_expr: str | None = None,
) -> List[Dict[str, Any]]:
    out = []
    for c in _CORNERS:
        item: Dict[str, Any] = {
            "id": f"{id_prefix}_{c.lower()}",
            "label": f"{label}_{c}",
            "unit": unit,
            "status": "file_go",
            "source": source.replace("{C}", c),
            "file_status": "file_go",
            "evidence": evidence,
            "corner": c,
        }
        if runtime_expr:
            item["runtime_map"] = {"expr": runtime_expr.replace("{i}", str(_CORNERS.index(c)))}
        out.append(item)
    return out


STREAM_CATALOG: List[Dict[str, Any]] = [
    # --- file GO ---
    {
        "id": "lap_time_ms",
        "label": "Lap time (current-lap timer)",
        "unit": "ms",
        "status": "file_go",
        "source": "rpy u32 ms stride-24 stream",
        "evidence": "Spa gold vs MoTeC TIME Δ≤7ms",
    },
    {
        "id": "weather_data",
        "label": "weatherData[7] raw",
        "unit": "raw_f32",
        "status": "file_go",
        "source": "rpy weather block",
        "evidence": "env_decode.decode_weather_data",
        "note": "Named air/road/grip still hypothesis",
    },
    {
        "id": "pose_xyz",
        "label": "Pose XYZ (rec_68)",
        "unit": "m",
        "status": "file_go",
        "source": "rec_68 f32[0:3] world XYZ",
        "file_status": "file_go",
        "evidence": "Matches ACC Cache fastlane centerline within ~3m (Spa)",
        "note": "Use with spline/heading for maps and corner overlays",
    },
    {
        "id": "spline",
        "label": "normalizedCarPosition",
        "unit": "0-1",
        "status": "file_go",
        "source": "rec_68 f32[23]",
        "file_status": "file_go",
        "evidence": "Lap wraps 0→1; path-speed |r|≈0.98 vs pose deltas (Spa)",
    },
    {
        "id": "heading_deg",
        "label": "heading (derived)",
        "unit": "deg",
        "status": "file_go",
        "source": "atan2(dx,dz) from rec_68 position deltas",
        "file_status": "file_go",
        "evidence": "Derived; no Motec heading channel in Epic .ld",
    },
    {
        "id": "dist_m",
        "label": "lap distance",
        "unit": "m",
        "status": "file_go",
        "source": "spline * track_config.length_m",
        "file_status": "file_go",
    },
    {
        "id": "track_centerline",
        "label": "ACC Cache fastlane centerline",
        "unit": "m XYZ",
        "status": "file_go",
        "source": "AC2/Content/Cache/<track>/fastlane.ai v8",
        "file_status": "file_go",
        "evidence": "Spa: 695 pts, ~6938m; pose nearest median ~3m",
    },
    {
        "id": "track_limits",
        "label": "track limits L/R",
        "unit": "m",
        "status": "file_go",
        "source": "ACC Cache fastlane.ai sec2 f32[20]: [1]=L_m [2]=R_m",
        "file_status": "file_go",
        "evidence": "Spa/Monza/Imola official widths; median track width ≈11.3/10.0/10.3 m",
        "note": "acc_fastlane.parse_acc_fastlane limits_official=True",
    },
    {
        "id": "is_valid_lap",
        "label": "isValidLap",
        "unit": "bool",
        "status": "file_go",
        "source": "arr_24 (lap-timer snapshot, stride 0x18) [+8] u8; 1=valid, drops to 0 at first cut until S/F",
        "file_status": "file_go",
        "evidence": (
            "empirical: flips repeat at Spa track-limit hotspots across 3 fixtures; "
            "Erik official 2:15.425 lap all-1; no Motec validity channel; writer "
            "not decompiled (RaceHUDState.IsLaptimeCurrentValid +0x14c is HUD side)"
        ),
        "runtime_map": {"hud_prop": "IsLaptimeCurrentValid", "obj": "RaceHUDState +0x14c"},
        "note": "lap_validity.decode_rpy_lap_validity; pose_track lap_segments[].valid_flag",
    },

    # --- runtime GO from audio FMOD path (FUN_140dc7610 / 140dc9320) ---
    {
        "id": "throttle",
        "label": "throttle",
        "unit": "0-1",
        "status": "file_go",
        "source": "arr_30[+4] u8/255 via FUN_140fdf850 ← physics+0x274",
        "runtime_map": {
            "expr": "*(float*)(audio+0x3ac) if mode==2 else 0",
            "audio_obj": "RCX / R13",
            "car_obj": "*(audio+0x2b8) via RBP",
            "asm": "140dc77cf MOVSS XMM2,[R13+0x3ac]",
        },
        "ue_property": "ThrottleInput (ReplicatedVehicleState neighbor)",
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec |r|≈0.77 (140s window); arr30_decode.py",
    },
    {
        "id": "brake_input",
        "label": "brake_input",
        "unit": "0-1",
        "status": "file_go",
        "source": "arr_30[+5] u8/255 via FUN_140fdf850 ← physics+0x278",
        "runtime_map": {
            "expr": "*(float*)(car+0xef0)",
            "asm": "140dc7901 MOVSS XMM2,[RBP+0xef0]",
        },
        "ue_property": "BrakeInput",
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec |r|≈0.86; same +0x278 as wheel brake_input",
    },
    {
        "id": "speed",
        "label": "speed",
        "unit": "km/h",
        "status": "file_go",
        "source": "arr_30[+14] u16*500/65535 via FUN_140fdf850 ← physics+0x288*3.6",
        "runtime_map": {
            "expr": "*(float*)(car+0xf00) * DAT_143be37bc",
            "asm": "140dc798d MOVSS XMM2,[RBP+0xf00]",
        },
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec |r|≈0.99 (140s window)",
    },
    {
        "id": "steer",
        "label": "steer_deg",
        "unit": "deg",
        "status": "file_go",
        "source": "arr_30[+2] -f16 via FUN_140fdf850 ← physics+0x270",
        "runtime_map": {
            "expr": "*(float*)(car+0xee8)  # runtime; file uses physics+0x270 pack",
        },
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec STEERANGLE |r|≈0.78 (sign-flipped f16)",
    },
    {
        "id": "gear",
        "label": "gear",
        "unit": "int (0=N)",
        "status": "file_go",
        "source": "arr_30[+32] ((u32>>5)&0xf)-1 via FUN_140fdf850 ← physics+0x280&0xf",
        "runtime_map": {
            "expr": "ReplicatedVehicleState.CurrentGear / physics+0x280 nibble",
        },
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec GEAR |r|≈0.96; 87% exact after nibble-1",
    },
    {
        "id": "rpm",
        "label": "rpm",
        "unit": "rpm (packed f16)",
        "status": "file_go",
        "source": "arr_30[+0] f16 ← physics+0x24c",
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec RPMS |r|≈0.81",
    },
    {
        "id": "g_lat",
        "label": "g_lat",
        "unit": "g",
        "status": "file_go",
        "source": "arr_30[+24] f16 ← physics+0x48c",
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec G_LAT |r|≈0.93",
    },
    {
        "id": "g_lon",
        "label": "g_lon",
        "unit": "g",
        "status": "file_go",
        "source": "arr_30[+28] f16 ← physics+0x494",
        "file_status": "file_go",
        "evidence": "Tomato Spa Motec G_LON |r|≈0.85",
    },
    *_corner_streams(
        "wheel_angular",
        "wheel_angular_speed",
        "rad/s (packed f16)",
        "arr_30 wheel f16 ← physics+0x260..0x26c ({C})",
        evidence="Motec WHEEL_SPEED |r|≈0.98; export also wheel_speed_ms=f16*0.337",
        runtime_expr="*(float*)(car+0x260+{i}*4)",
    ),
    *_corner_streams(
        "disc_temp",
        "disc_temp",
        "C",
        "arr_48[+50..] f16 ← physics+0x6c0+i*4 ({C})",
        evidence="Tomato Spa Motec BRAKE_TEMP |r|≈0.97 (range match °C)",
        runtime_expr="*(float*)(car+0x6c0+{i}*4)",
    ),
    *_corner_streams(
        "tyre_load",
        "tyre_load",
        "N (packed f16)",
        "arr_48[+8..] f16 ← physics+0x2e4+i*4 ({C})",
        evidence="code-backed FUN_140fdf850; no Motec tyre_load channel (SUS proxy |r|≈0.7)",
        runtime_expr="*(float*)(car+0x2e4+{i}*4)",
    ),
    *_corner_streams(
        "slip_ratio",
        "slip_ratio",
        "ratio",
        "arr_48[+16..] f16 ← physics+0x2b4+i*4 ({C})",
        evidence="code-backed FUN_140fdf850 ← same offset as audio slip_ratio",
        runtime_expr="*(float*)(car+0x2b4+{i}*4)",
    ),
    *_corner_streams(
        "wheel_dirt",
        "wheel_dirt",
        "0-1",
        "arr_48[+46..] u8/255 ← physics+0x324+i*4 ({C})",
        evidence="code-backed FUN_140fdf850",
        runtime_expr="*(float*)(car+0x324+{i}*4)",
    ),
    # still runtime-only / not in packer layout yet
    {
        "id": "drivetrain_speed",
        "label": "drivetrain_speed",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(car+0x11d0)"},
        "file_status": "no_go",
    },
    {
        "id": "torque",
        "label": "torque",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(car+0xed0)"},
        "file_status": "no_go",
    },
    {
        "id": "limiter",
        "label": "limiter",
        "unit": "bool→float",
        "status": "runtime_go",
        "runtime_map": {"expr": "(float)*(uint8*)(car+0xed4)"},
        "file_status": "no_go",
    },
    {
        "id": "tc_level",
        "label": "TC / TC1 setting (map)",
        "unit": "map",
        "status": "runtime_go",
        "runtime_map": {"expr": "graphics.TC / HUD TC_Level*"},
        "file_status": "no_go",
        "note": "SETTING not state. Pass via --electronics until .rpy lane Motec-locks.",
    },
    {
        "id": "tc_cut_level",
        "label": "TCCut / TC2 setting",
        "unit": "map",
        "status": "runtime_go",
        "runtime_map": {"expr": "graphics.TCCut / HUD TCCut_Level*"},
        "file_status": "no_go",
        "note": "SETTING. Distinct from TC1.",
    },
    {
        "id": "tc_in_action",
        "label": "TC intervention (active)",
        "unit": "flag",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(u8*)(physics+0x8f8) = *(u8*)(Car+0x1468); Motec TC logger+0xf4",
            "asm": "FUN_14347b010 @14347b0dc; Motec 1434a2225 CMP byte [RAX+0x1468]",
        },
        "file_status": "no_go",
        "note": "STATE. Export VRE_TC_ACT wheelspin proxy until packer Motec-locks.",
    },
    {
        "id": "traction_control",
        "label": "traction_control (audio FMOD)",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "XMM8 from car+0x1570 gate"},
        "file_status": "no_go",
        "note": "FMOD param; prefer tc_in_action / tc_level split above.",
    },
    {
        "id": "wheel_brake_input",
        "label": "brake_input (wheel updater)",
        "unit": "0-1",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(RSI+0x278)"},
        "file_status": "file_go",
        "note": "Same physics+0x278 as car brake_input in arr_30[+5]",
    },
    {
        "id": "nd_slip",
        "label": "nd_slip (per wheel)",
        "unit": "raw",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "*(float*)(physics+0x2c4+i*4)"},
        "evidence": "FUN_140fdf850 arr_48[+0..+6] f16; FMOD nd_slip; |r|≈0.64 vs |slip_ratio|",
    },
    {
        "id": "marbles_level",
        "label": "marbles_level (per wheel)",
        "unit": "0-1",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+0x724 (FMOD marbles_level)"},
        "evidence": "FUN_140fdf850 arr_48[+26..+29] u8/255",
    },
    {
        "id": "is_on_marbles",
        "label": "is_on_marbles (per wheel)",
        "unit": "flag",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+0x8e8+i*4 (FMOD is_on_marbles)"},
        "evidence": "FUN_140fdf850 arr_48[+30..+33] u8 0|255; Tomato Spa binary 0/255 only",
    },
    {
        "id": "sus_proxy",
        "label": "sus_proxy (per wheel, from physics+0x478)",
        "unit": "0-1",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+0x478+i*4"},
        "evidence": (
            "FUN_140fdf850 arr_48[+34..+37] u8/255; Tomato Motec SUS_TRAVEL "
            "same-corner |r_res|≈0.80 (lag SPEED-locked). Not millimetres."
        ),
        "note": "Export as SUS_PROXY_* / VRE_SUS_PROXY_*; prefer over tyre_load rake when present.",
    },
    {
        "id": "phys_500",
        "label": "physics+0x500 (per wheel, name TBD)",
        "unit": "raw",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+0x500+i*4"},
        "evidence": "FUN_140fdf850 arr_48[+38..+44] f16; also |r| vs Motec SUS ~0.6",
        "note": "Code-backed; not Motec-named. Do not coach as PRESS/TAIR.",
    },
    {
        "id": "susp_damage_level",
        "label": "susp_damage_level",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(RSI+R14*4+0x5bc)"},
        "file_status": "no_go",
    },
    {
        "id": "rain_level",
        "label": "rain_level",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(RSI+R14*4+0x798)"},
        "file_status": "no_go",
    },
    {
        "id": "tyre_press",
        "label": "tyre pressure (PSI, live)",
        "unit": "psi",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(float*)(physics+0x5e4+i*0x3c) (0 if Car+0x1868+i*0x1040 <= 0)",
            "sim_car": "Car+0x1854+i*0x1040 (Motec TYRE_PRESS source ptr, ctor 14349d090)",
            "asm": "FUN_14347b010 @14347b63f..b653: comiss; MOV EAX,[RBX-0x18c]; MOV [R15],EAX",
        },
        "file_status": "no_go",
        "note": (
            "Code-backed 2026-11-19. physics+0x5ac+i*4 is NOT PSI: FMOD "
            "inflation = 1.0 - [0x5ac] (const @143a3d3b0 == 1.0); it mirrors "
            "Car+0x1868+i*0x1040. Pressure lane physics+0x5e4 is not read by "
            "FUN_140fdf850 / fe0490 / fe0ea0 / fe3a80 (only memcpy'd in-memory "
            "by FUN_140e7aa90 → entry+0x468, never serialized)."
        ),
    },
    {
        "id": "tyre_tair",
        "label": "tyre air temp",
        "unit": "C",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(float*)(physics+0x5dc+i*0x3c) = (float)*(double*)(Car+0x1a78+i*0x1040)",
            "asm": "FUN_14347b010 @14347b604: MOVSD XMM0,[RBX+0x98]; CVTPD2PS; MOVSS [R15-8]",
        },
        "file_status": "no_go",
        "note": "Same as tyre_press: physics-state only; no file packer reads +0x5dc.",
    },
    {
        "id": "sus_travel",
        "label": "suspension travel",
        "unit": "mm",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(float*)(physics+0x530+i*4) [m] * 1000 = Motec SUS_TRAVEL_*",
            "sim_car": "susp_i=*(Car+0x5640)[i]; (*susp->vfunc[0x58])()[0] (float m)",
            "asm": "FUN_14347b010 @14347c0c9..c0d6; Motec 1434a2270 (xmm6=1000.0 @143a416b4)",
        },
        "file_status": "no_go",
        "note": (
            "physics+0x530 not read by FUN_140fdf850 (also physics+0x540 = susp+8 "
            "float). Copy-only in recorder chain."
        ),
    },
    {
        "id": "abs_level",
        "label": "ABS setting (map)",
        "unit": "map",
        "status": "runtime_go",
        "runtime_map": {"expr": "graphics.ABS / HUD ABS_Level*"},
        "file_status": "no_go",
        "note": "SETTING e.g. ABS=2. Pass via --electronics until .rpy Motec-locks.",
    },
    {
        "id": "abs_in_action",
        "label": "ABS intervention (active)",
        "unit": "flag",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(u8*)(physics+0x8f9) = *(u8*)(Car+0x1430); Motec ABS logger+0xf8",
            "asm": "FUN_14347b010 @14347b0cf; Motec 1434a223e CMP byte [RAX+0x1430]",
        },
        "file_status": "no_go",
        "note": "STATE. Export VRE_ABS_ACT front-lock+brake proxy until packer Motec-locks.",
    },
    {
        "id": "abs",
        "label": "ABS (legacy alias → prefer abs_level / abs_in_action)",
        "unit": "flag",
        "status": "runtime_go",
        "file_status": "no_go",
        "note": "Ambiguous: Motec ABS is state; garage ABS is setting.",
    },

    # UE ReplicatedVehicleState (0x14 bytes) — property table 0x144b79d68
    {
        "id": "ue_steering_input",
        "label": "SteeringInput",
        "unit": "raw",
        "status": "runtime_go",
        "source": "ReplicatedVehicleState+0x00",
        "runtime_map": {"struct": "ReplicatedVehicleState", "offset": 0x00, "size": 4},
        "file_status": "file_go",
        "note": "File lane is physics+0x270 via arr_30[+2]; RVS packing not separately locked.",
    },
    {
        "id": "ue_throttle_input",
        "label": "ThrottleInput",
        "unit": "0-1",
        "status": "runtime_go",
        "source": "ReplicatedVehicleState+0x04",
        "runtime_map": {"struct": "ReplicatedVehicleState", "offset": 0x04, "size": 4},
        "file_status": "no_go",
    },
    {
        "id": "ue_brake_input",
        "label": "BrakeInput",
        "unit": "0-1",
        "status": "runtime_go",
        "source": "ReplicatedVehicleState+0x08",
        "runtime_map": {"struct": "ReplicatedVehicleState", "offset": 0x08, "size": 4},
        "file_status": "no_go",
    },
    {
        "id": "ue_handbrake_input",
        "label": "HandbrakeInput",
        "unit": "raw",
        "status": "runtime_go",
        "source": "ReplicatedVehicleState+0x0C",
        "runtime_map": {"struct": "ReplicatedVehicleState", "offset": 0x0C, "size": 4},
        "file_status": "no_go",
    },
    {
        "id": "ue_current_gear",
        "label": "CurrentGear",
        "unit": "int",
        "status": "runtime_go",
        "source": "ReplicatedVehicleState+0x10",
        "runtime_map": {"struct": "ReplicatedVehicleState", "offset": 0x10, "size": 4},
        "file_status": "file_go",
        "note": "File lane is physics+0x280 nibble via arr_30[+32]; Motec = nibble-1.",
    },
    {
        "id": "ue_gas_pedal_position",
        "label": "GasPedalPosition",
        "unit": "0-1",
        "status": "runtime_go",
        "source": "UAcCarAnimations neighbor string @ 143c56f28",
        "file_status": "no_go",
        "note": "Animation/visual pedal; may lag physics ThrottleInput",
    },
]


def catalog_by_status() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for s in STREAM_CATALOG:
        out.setdefault(s["status"], []).append(s["id"])
    return out


def catalog_json() -> Dict[str, Any]:
    return {
        "schema": "vre.acc_rpy.stream_catalog.v1",
        "artifact": "docs/superpowers/specs/ghidra-acc-out/stream_map_all.txt",
        "by_status": catalog_by_status(),
        "streams": STREAM_CATALOG,
    }
