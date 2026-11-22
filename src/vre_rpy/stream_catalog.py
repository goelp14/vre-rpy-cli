"""Channel catalog for .rpy exports (file-backed streams + Motec calcs)."""



from __future__ import annotations

from typing import Any, Dict, List

# Status:
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
        "evidence": "file_backed",
    },
    {
        "id": "weather_data",
        "label": "weatherData[7] raw",
        "unit": "raw_f32",
        "status": "file_go",
        "source": "rpy weather block",
        "evidence": "file_backed",
        "note": "Named air/road/grip still hypothesis",
    },
    {
        "id": "pose_xyz",
        "label": "Pose XYZ (rec_68)",
        "unit": "m",
        "status": "file_go",
        "source": "rec_68 f32[0:3] world XYZ",
        "file_status": "file_go",
        "evidence": "file_backed",
        "note": "Use with spline/heading for maps and corner overlays",
    },
    {
        "id": "spline",
        "label": "normalizedCarPosition",
        "unit": "0-1",
        "status": "file_go",
        "source": "rec_68 f32[23]",
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "heading_deg",
        "label": "heading (derived)",
        "unit": "deg",
        "status": "file_go",
        "source": "atan2(dx,dz) from rec_68 position deltas",
        "file_status": "file_go",
        "evidence": "file_backed",
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
        "evidence": "file_backed",
    },
    {
        "id": "track_limits",
        "label": "track limits L/R",
        "unit": "m",
        "status": "file_go",
        "source": "ACC Cache fastlane.ai sec2 f32[20]: [1]=L_m [2]=R_m",
        "file_status": "file_go",
        "evidence": "file_backed",
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
            "empirical: flips repeat at Spa track-limit hotspots across fixtures; "
            "clean flying laps stay valid; no Motec validity channel"
        ),
        "note": "lap_validity.decode_rpy_lap_validity; pose_track lap_segments[].valid_flag",
    },

    # --- runtime GO (in-game / shared-memory class sources) ---
    {
        "id": "throttle",
        "label": "throttle",
        "unit": "0-1",
        "status": "file_go",
        "source": "arr_30[+4] u8/255 via decoder ← physics+offset",
        "runtime_map": {
            "expr": "*(float*)(audio+offset) if mode==2 else 0",
            "audio_obj": "RCX / R13",
            "car_obj": "*(audio+offset) via RBP",
            "asm": "140dc77cf MOVSS XMM2,[R13+offset]",
        },
        "ue_property": "ThrottleInput (ReplicatedVehicleState neighbor)",
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "brake_input",
        "label": "brake_input",
        "unit": "0-1",
        "status": "file_go",
        "source": "arr_30[+5] u8/255 via decoder ← physics+offset",
        "runtime_map": {
            "expr": "*(float*)(car+offset)",
            "asm": "140dc7901 MOVSS XMM2,[RBP+offset]",
        },
        "ue_property": "BrakeInput",
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "speed",
        "label": "speed",
        "unit": "km/h",
        "status": "file_go",
        "source": "arr_30[+14] u16*500/65535 via decoder ← physics+offset*3.6",
        "runtime_map": {
            "expr": "*(float*)(car+offset) * DAT_143be37bc",
            "asm": "140dc798d MOVSS XMM2,[RBP+offset]",
        },
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "steer",
        "label": "steer_deg",
        "unit": "deg",
        "status": "file_go",
        "source": "arr_30[+2] -f16 via decoder ← physics+offset",
        "runtime_map": {
            "expr": "*(float*)(car+offset)  # runtime; file uses physics+offset pack",
        },
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "gear",
        "label": "gear",
        "unit": "int (0=N)",
        "status": "file_go",
        "source": "arr_30[+32] ((u32>>5)&0xf)-1 via decoder ← physics+offset&0xf",
        "runtime_map": {
            "expr": "ReplicatedVehicleState.CurrentGear / physics+offset nibble",
        },
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "rpm",
        "label": "rpm",
        "unit": "rpm (packed f16)",
        "status": "file_go",
        "source": "arr_30[+0] f16 ← physics+offset",
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "g_lat",
        "label": "g_lat",
        "unit": "g",
        "status": "file_go",
        "source": "arr_30[+24] f16 ← physics+offset",
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "g_lon",
        "label": "g_lon",
        "unit": "g",
        "status": "file_go",
        "source": "arr_30[+28] f16 ← physics+offset",
        "file_status": "file_go",
        "evidence": "file_backed",
    },
    {
        "id": "vel_xyz",
        "label": "vel_x / vel_y / vel_z",
        "unit": "m/s (world, Y up)",
        "status": "file_go",
        "source": "arr_30[+16/+18/+20] f16",
        "file_status": "file_go",
        "evidence": "|v| vs speed r=0.9999995, ratio 0.9997 (Spa gold)",
    },
    {
        "id": "g_vert",
        "label": "g_vert",
        "unit": "g (deviation from 1 g)",
        "status": "file_go",
        "source": "arr_30[+26] f16 (Y of the G vector)",
        "file_status": "file_go",
        "evidence": "vs d(vel_y)/dt r=0.886 at 11 Hz (Spa)",
    },
    {
        "id": "drivetrain_speed",
        "label": "drivetrain_speed",
        "unit": "unconfirmed (~0.8x km/h under power)",
        "status": "hypothesis",
        "source": "arr_30[+22] f16",
        "file_status": "file_go",
        "evidence": "speed r=0.994, Motec CLUTCH r=0.92; name not confirmed",
    },
    {
        "id": "wheel_contact_xyz",
        "label": "wheel contact points ×4",
        "unit": "m (world)",
        "status": "file_go",
        "source": "pose rec_68 f32[5:17] (front +side, front -side, rear +side, rear -side)",
        "file_status": "file_go",
        "evidence": "296: front track 1.73 m, rear 1.71 m, wheelbase 2.66 m",
    },
    {
        "id": "tyre_radius",
        "label": "tyre radius ×4",
        "unit": "m",
        "status": "file_go",
        "source": "snapshot header (stride 16 × 1) f32 [LF, RF, LR, RR]",
        "file_status": "file_go",
        "evidence": "Motec WHEEL_SPEED radius within 0.8 % (Spa gold)",
    },
    {
        "id": "predicted_lap_ms",
        "label": "ACC predicted lap time",
        "unit": "ms",
        "status": "file_go_empirical",
        "source": "lap-timer stride 24 [+4] u32 (0x7fffffff = none)",
        "file_status": "file_go",
        "evidence": "varies within the lap; present only once a valid flying lap exists",
    },
    *_corner_streams(
        "wheel_angular",
        "wheel_angular_speed",
        "rad/s (packed f16)",
        "arr_30 wheel f16 ← physics+offset..offset ({C})",
        evidence="Motec WHEEL_SPEED |r|≈0.98; wheel_speed_ms = f16 * per-car tyre radius (snapshot header)",
        runtime_expr="*(float*)(car+offset+{i}*4)",
    ),
    *_corner_streams(
        "disc_temp",
        "disc_temp",
        "C",
        "arr_48[+50..] f16 ← physics+offset+i*4 ({C})",
        evidence="Tomato Spa Motec BRAKE_TEMP |r|≈0.97 (range match °C)",
        runtime_expr="*(float*)(car+offset+{i}*4)",
    ),
    *_corner_streams(
        "tyre_load",
        "tyre_load",
        "N (packed f16)",
        "arr_48[+8..] f16 ← physics+offset+i*4 ({C})",
        evidence="code-backed decoder; no Motec tyre_load channel (SUS proxy |r|≈0.7)",
        runtime_expr="*(float*)(car+offset+{i}*4)",
    ),
    *_corner_streams(
        "slip_ratio",
        "slip_ratio",
        "ratio",
        "arr_48[+16..] f16 ← physics+offset+i*4 ({C})",
        evidence="code-backed decoder ← same offset as audio slip_ratio",
        runtime_expr="*(float*)(car+offset+{i}*4)",
    ),
    *_corner_streams(
        "wheel_dirt",
        "wheel_dirt",
        "0-1",
        "arr_48[+46..] u8/255 ← physics+offset+i*4 ({C})",
        evidence="code-backed decoder",
        runtime_expr="*(float*)(car+offset+{i}*4)",
    ),
    # still runtime-only / not in packer layout yet
    {
        "id": "drivetrain_speed",
        "label": "drivetrain_speed",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(car+offset)"},
        "file_status": "no_go",
    },
    {
        "id": "torque",
        "label": "torque",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(car+offset)"},
        "file_status": "no_go",
    },
    {
        "id": "limiter",
        "label": "limiter",
        "unit": "bool→float",
        "status": "runtime_go",
        "runtime_map": {"expr": "(float)*(uint8*)(car+offset)"},
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
            "expr": "*(u8*)(physics+offset) = *(u8*)(Car+offset); Motec TC logger+0xf4",
            "asm": "decoder @14347b0dc; Motec 1434a2225 CMP byte [RAX+offset]",
        },
        "file_status": "no_go",
        "note": "STATE. Export VRE_TC_ACT wheelspin proxy until packer Motec-locks.",
    },
    {
        "id": "traction_control",
        "label": "traction_control (audio FMOD)",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "XMM8 from car+offset gate"},
        "file_status": "no_go",
        "note": "FMOD param; prefer tc_in_action / tc_level split above.",
    },
    {
        "id": "wheel_brake_input",
        "label": "brake_input (wheel updater)",
        "unit": "0-1",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(RSI+offset)"},
        "file_status": "file_go",
        "note": "Same physics+offset as car brake_input in arr_30[+5]",
    },
    {
        "id": "nd_slip",
        "label": "nd_slip (per wheel)",
        "unit": "raw",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "*(float*)(physics+offset+i*4)"},
        "evidence": "file_backed",
    },
    {
        "id": "marbles_level",
        "label": "marbles_level (per wheel)",
        "unit": "0-1",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+offset (FMOD marbles_level)"},
        "evidence": "file_backed",
    },
    {
        "id": "is_on_marbles",
        "label": "is_on_marbles (per wheel)",
        "unit": "flag",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+offset+i*4 (FMOD is_on_marbles)"},
        "evidence": "file_backed",
    },
    {
        "id": "sus_proxy",
        "label": "sus_proxy (per wheel, from physics+offset)",
        "unit": "0-1",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+offset+i*4"},
        "evidence": (
            "decoder arr_48[+34..+37] u8/255; Tomato Motec SUS_TRAVEL "
            "same-corner |r_res|≈0.80 (lag SPEED-locked). Not millimetres."
        ),
        "note": "Export as SUS_PROXY_* / VRE_SUS_PROXY_*; prefer over tyre_load rake when present.",
    },
    {
        "id": "phys_500",
        "label": "physics+offset (per wheel, name TBD)",
        "unit": "raw",
        "status": "file_go",
        "file_status": "file_go",
        "runtime_map": {"expr": "physics+offset+i*4"},
        "evidence": "file_backed",
        "note": "Code-backed; not Motec-named. Do not coach as PRESS/TAIR.",
    },
    {
        "id": "susp_damage_level",
        "label": "susp_damage_level",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(RSI+R14*4+offset)"},
        "file_status": "no_go",
    },
    {
        "id": "rain_level",
        "label": "rain_level",
        "unit": "raw",
        "status": "runtime_go",
        "runtime_map": {"expr": "*(float*)(RSI+R14*4+offset)"},
        "file_status": "no_go",
    },
    {
        "id": "tyre_press",
        "label": "tyre pressure (PSI, live)",
        "unit": "psi",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(float*)(physics+offset+i*0x3c) (0 if Car+offset+i*offset <= 0)",
            "sim_car": "Car+offset+i*offset (Motec TYRE_PRESS source ptr, ctor 14349d090)",
            "asm": "decoder @14347b63f..b653: comiss; MOV EAX,[RBX-offset]; MOV [R15],EAX",
        },
        "file_status": "no_go",
        "note": (
            "Code-backed 2026-11-19. physics+offset+i*4 is NOT PSI: FMOD "
            "inflation = 1.0 - [offset] (const @143a3d3b0 == 1.0); it mirrors "
            "Car+offset+i*offset. Pressure lane physics+offset is not read by "
            "decoder / fe0490 / fe0ea0 / fe3a80 (only memcpy'd in-memory "
            "by decoder → entry+offset, never serialized)."
        ),
    },
    {
        "id": "tyre_tair",
        "label": "tyre air temp",
        "unit": "C",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(float*)(physics+offset+i*0x3c) = (float)*(double*)(Car+offset+i*offset)",
            "asm": "decoder @14347b604: MOVSD XMM0,[RBX+0x98]; CVTPD2PS; MOVSS [R15-8]",
        },
        "file_status": "no_go",
        "note": "Same as tyre_press: physics-state only; no file packer reads +offset.",
    },
    {
        "id": "sus_travel",
        "label": "suspension travel",
        "unit": "mm",
        "status": "runtime_go",
        "runtime_map": {
            "expr": "*(float*)(physics+offset+i*4) [m] * 1000 = Motec SUS_TRAVEL_*",
            "sim_car": "susp_i=*(Car+offset)[i]; (*susp->vfunc[0x58])()[0] (float m)",
            "asm": "decoder @14347c0c9..c0d6; Motec 1434a2270 (xmm6=1000.0 @143a416b4)",
        },
        "file_status": "no_go",
        "note": (
            "physics+offset not read by decoder (also physics+offset = susp+8 "
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
            "expr": "*(u8*)(physics+offset) = *(u8*)(Car+offset); Motec ABS logger+0xf8",
            "asm": "decoder @14347b0cf; Motec 1434a223e CMP byte [RAX+offset]",
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

    # UE ReplicatedVehicleState (0x14 bytes) — property table offset
    {
        "id": "ue_steering_input",
        "label": "SteeringInput",
        "unit": "raw",
        "status": "runtime_go",
        "source": "ReplicatedVehicleState+0x00",
        "runtime_map": {"struct": "ReplicatedVehicleState", "offset": 0x00, "size": 4},
        "file_status": "file_go",
        "note": "File lane is physics+offset via arr_30[+2]; RVS packing not separately locked.",
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
        "note": "File lane is physics+offset nibble via arr_30[+32]; Motec = nibble-1.",
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
        "artifact": "docs/superpowers/specs/decoder-acc-out/stream_map_all.txt",
        "by_status": catalog_by_status(),
        "streams": STREAM_CATALOG,
    }
