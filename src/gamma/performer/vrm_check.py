"""Check a .vrm model before using it with Shana's 3D adapter.

    python -m gamma.performer.vrm_check path/to/shana.vrm

A .vrm is a binary glTF file whose JSON part describes the avatar. This reads
that JSON only (no 3D libraries) and reports:

- the VRM version (0.x from VRoid's older exports, or 1.0) and which
  ``avatar3d_naming`` matches the expression names in the file;
- whether every expression the adapter sends exists (blink, the five mouth
  shapes, and the emotions in ``performer/avatar3d.py``);
- whether the humanoid bones VRM requires are mapped;
- whether hair/skirt spring bones exist;
- the licence fields, which decide whether streaming and commercial use are
  allowed.
"""
from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .avatar3d import VRM0_BLINK, VRM0_EXPRESSIONS, VRM0_MOUTH, VRM1_BLINK, VRM1_EXPRESSIONS, VRM1_MOUTH

REQUIRED_BONES = ("hips", "spine", "head", "leftUpperArm", "leftLowerArm", "leftHand", "rightUpperArm",
                  "rightLowerArm", "rightHand", "leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg",
                  "rightLowerLeg", "rightFoot")
# VRM 0.x preset names as VRoid writes them (lowercase) -> the names VMC receivers use.
_VRM0_PRESETS = {"joy": "Joy", "angry": "Angry", "sorrow": "Sorrow", "fun": "Fun", "a": "A", "i": "I", "u": "U",
                 "e": "E", "o": "O", "blink": "Blink", "blink_l": "Blink_L", "blink_r": "Blink_R", "neutral": "Neutral"}


@dataclass
class Report:
    version: str = "not a VRM"
    naming: str = ""
    expressions: list[str] = field(default_factory=list)
    missing_expressions: list[str] = field(default_factory=list)
    missing_bones: list[str] = field(default_factory=list)
    spring_groups: int = 0
    licence: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def read_gltf_json(data: bytes) -> dict:
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("not a binary glTF/VRM file")
    length, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise ValueError("the first chunk is not JSON")
    return json.loads(data[20:20 + length].decode("utf-8"))


def _needed(naming: str) -> set[str]:
    expressions = VRM1_EXPRESSIONS if naming == "vrm1" else VRM0_EXPRESSIONS
    mouth = VRM1_MOUTH if naming == "vrm1" else VRM0_MOUTH
    blink = VRM1_BLINK if naming == "vrm1" else VRM0_BLINK
    return {name for shape in expressions.values() for name in shape} | set(mouth.values()) | {blink}


def check(gltf: dict) -> Report:
    report = Report()
    extensions = gltf.get("extensions") or {}
    if "VRMC_vrm" in extensions:
        vrm = extensions["VRMC_vrm"]
        report.version, report.naming = f"1.0 ({vrm.get('specVersion', '1.0')})", "vrm1"
        preset = (vrm.get("expressions") or {}).get("preset") or {}
        custom = (vrm.get("expressions") or {}).get("custom") or {}
        report.expressions = sorted(set(preset) | set(custom))
        bones = set((vrm.get("humanoid") or {}).get("humanBones") or {})
        springs = (extensions.get("VRMC_springBone") or {}).get("springs") or []
        meta = vrm.get("meta") or {}
        report.licence = {k: str(meta.get(k)) for k in ("name", "authors", "avatarPermission", "commercialUsage",
                                                         "allowRedistribution", "licenseUrl") if k in meta}
    elif "VRM" in extensions:
        vrm = extensions["VRM"]
        report.version, report.naming = "0.x", "vrm0"
        groups = (vrm.get("blendShapeMaster") or {}).get("blendShapeGroups") or []
        names = set()
        for group in groups:
            preset = str(group.get("presetName") or "").lower()
            names.add(_VRM0_PRESETS.get(preset) or str(group.get("name") or ""))
        report.expressions = sorted(n for n in names if n)
        bones = {str(b.get("bone")) for b in (vrm.get("humanoid") or {}).get("humanBones") or []}
        springs = (vrm.get("secondaryAnimation") or {}).get("boneGroups") or []
        meta = vrm.get("meta") or {}
        report.licence = {k: str(meta.get(k)) for k in ("title", "author", "allowedUserName", "commercialUssageName",
                                                         "violentUssageName", "licenseName") if k in meta}
    else:
        report.problems.append("No VRM extension: export from VRoid Studio as VRM, not plain glTF.")
        return report
    present = set(report.expressions)
    report.missing_expressions = sorted(_needed(report.naming) - present)
    report.missing_bones = [b for b in REQUIRED_BONES if b not in bones]
    report.spring_groups = len(springs)
    if report.missing_expressions:
        report.problems.append("Missing expressions Shana uses: " + ", ".join(report.missing_expressions)
                               + ". Add them in VRoid, or map other names in [avatar3d.expressions].")
    if report.missing_bones:
        report.problems.append("Humanoid bones not mapped: " + ", ".join(report.missing_bones) + ".")
    return report


def render(report: Report) -> str:
    lines = [f"VRM version: {report.version}"]
    if report.naming:
        lines.append(f'Set avatar3d naming = "{report.naming}" to match this file.')
        lines.append(f"Expressions in the file ({len(report.expressions)}): {', '.join(report.expressions)}")
        lines.append(f"Spring bone groups (hair, skirt...): {report.spring_groups}"
                     + ("" if report.spring_groups else " - hair and skirt won't move"))
        for key, value in report.licence.items():
            lines.append(f"Licence {key}: {value}")
    lines += [f"PROBLEM: {p}" for p in report.problems] or ["Everything the 3D adapter needs is present."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a .vrm model for Shana's 3D adapter.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        report = check(read_gltf_json(args.path.read_bytes()))
    except (OSError, ValueError) as exc:
        print(f"Can't read {args.path}: {exc}")
        return 2
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
