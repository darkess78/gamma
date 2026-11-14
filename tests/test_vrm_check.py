"""The .vrm checker reads VRoid exports and reports what Shana's 3D adapter needs."""
import json
import struct

import pytest

from gamma.performer.vrm_check import REQUIRED_BONES, check, main, read_gltf_json, render


def glb(document: dict) -> bytes:
    body = json.dumps(document).encode()
    body += b" " * (-len(body) % 4)
    return b"glTF" + struct.pack("<II", 2, 20 + len(body)) + struct.pack("<I4s", len(body), b"JSON") + body


def vrm1(expressions, springs=1):
    return {"extensions": {
        "VRMC_vrm": {"specVersion": "1.0", "meta": {"name": "Shana", "commercialUsage": "personalNonProfit"},
                     "humanoid": {"humanBones": {b: {"node": i} for i, b in enumerate(REQUIRED_BONES)}},
                     "expressions": {"preset": {e: {} for e in expressions}}},
        "VRMC_springBone": {"springs": [{}] * springs}}}


VRM1_ALL = ["happy", "angry", "sad", "relaxed", "surprised", "aa", "ih", "ou", "ee", "oh", "blink", "blinkLeft",
            "blinkRight", "neutral"]


def test_complete_vrm1_export_passes_and_names_the_setting(tmp_path):
    report = check(read_gltf_json(glb(vrm1(VRM1_ALL))))
    assert report.ok and report.naming == "vrm1" and report.spring_groups == 1
    assert 'naming = "vrm1"' in render(report) and report.licence["commercialUsage"] == "personalNonProfit"
    path = tmp_path / "shana.vrm"; path.write_bytes(glb(vrm1(VRM1_ALL)))
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["vrm_check", str(path)]):
        assert main() == 0


def test_missing_expressions_and_bones_are_named():
    document = vrm1([e for e in VRM1_ALL if e not in ("surprised", "oh")], springs=0)
    del document["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["leftFoot"]
    report = check(document)
    assert report.missing_expressions == ["oh", "surprised"] and report.missing_bones == ["leftFoot"]
    assert not report.ok and "hair and skirt won't move" in render(report)


def test_vrm0_presets_map_to_vmc_names():
    groups = [{"presetName": p, "name": p} for p in ("joy", "angry", "sorrow", "fun", "a", "i", "u", "e", "o",
                                                       "blink", "blink_l", "blink_r", "neutral")]
    groups.append({"presetName": "unknown", "name": "Surprised"})
    document = {"extensions": {"VRM": {"blendShapeMaster": {"blendShapeGroups": groups},
                                       "humanoid": {"humanBones": [{"bone": b, "node": 0} for b in REQUIRED_BONES]},
                                       "secondaryAnimation": {"boneGroups": [{}, {}]},
                                       "meta": {"title": "Shana", "commercialUssageName": "Disallow"}}}}
    report = check(document)
    assert report.ok and report.naming == "vrm0" and "Joy" in report.expressions and report.spring_groups == 2
    assert report.licence["commercialUssageName"] == "Disallow"


def test_plain_gltf_and_garbage_are_refused():
    assert not check({"asset": {"version": "2.0"}}).ok
    with pytest.raises(ValueError):
        read_gltf_json(b"not a model at all")
