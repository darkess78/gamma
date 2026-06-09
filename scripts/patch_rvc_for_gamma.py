from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _replace_once(content: str, old: str, new: str, *, label: str) -> str:
    if new in content:
        return content
    if old not in content:
        raise RuntimeError(f"Could not find expected text for {label}.")
    return content.replace(old, new, 1)


def _patch_infer_cli(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = original
    updated = _replace_once(
        updated,
        '    parser.add_argument("--protect", type=float, default=0.33, help="protect")\n',
        '    parser.add_argument("--protect", type=float, default=0.33, help="protect")\n'
        '    parser.add_argument("--formant", type=float, default=0.0, help="formant shift in semitones")\n',
        label="infer_cli formant arg",
    )
    updated = _replace_once(
        updated,
        "        args.protect,\n    )\n",
        "        args.protect,\n        args.formant,\n    )\n",
        label="infer_cli vc_single formant arg",
    )
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def _patch_modules(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = original
    updated = _replace_once(
        updated,
        "        protect,\n    ):\n",
        "        protect,\n        formant=0.0,\n    ):\n",
        label="modules vc_single signature",
    )
    updated = _replace_once(
        updated,
        "                protect,\n                f0_file,\n            )\n",
        "                protect,\n                formant,\n                f0_file,\n            )\n",
        label="modules pipeline formant call",
    )
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def _patch_pipeline(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = original
    updated = _replace_once(
        updated,
        "        protect,\n        f0_file=None,\n    ):\n",
        "        protect,\n        formant=0.0,\n        f0_file=None,\n    ):\n",
        label="pipeline signature",
    )
    updated = _replace_once(
        updated,
        "                f0_up_key,\n",
        "                f0_up_key - formant,\n",
        label="pipeline get_f0 formant shift",
    )
    updated = _replace_once(
        updated,
        "        audio_opt = np.concatenate(audio_opt)\n",
        "        audio_opt = np.concatenate(audio_opt)\n"
        "        factor = pow(2, formant / 12)\n"
        "        if factor != 1:\n"
        "            adjusted_sr = max(1, int(np.round(tgt_sr * factor)))\n"
        "            audio_opt = librosa.resample(audio_opt, orig_sr=adjusted_sr, target_sr=tgt_sr)\n",
        label="pipeline output formant resample",
    )
    if updated != original:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def _discover_repo_root() -> Path | None:
    script_path = Path(__file__).resolve()
    search_bases = [script_path.parents[1], *script_path.parents[1:5], Path.home(), Path.home() / "Projects"]
    candidates: list[Path] = []
    for base in search_bases:
        candidates.extend(
            [
                base / "RVC" / "Retrieval-based-Voice-Conversion-WebUI-main",
                base / "Retrieval-based-Voice-Conversion-WebUI-main",
                base / "data" / "RVC" / "Retrieval-based-Voice-Conversion-WebUI-main",
            ]
        )
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "tools" / "infer_cli.py").exists() and (resolved / "assets" / "weights").exists():
            return resolved
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch an RVC checkout with Gamma's offline CLI formant support.")
    parser.add_argument(
        "--repo-root",
        default="",
        help="Path to the Retrieval-based-Voice-Conversion-WebUI-main checkout.",
    )
    parser.add_argument("--check", action="store_true", help="Validate whether the patch is already present.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else _discover_repo_root()
    if repo_root is None:
        print("Could not find the RVC checkout. Pass --repo-root explicitly.", file=sys.stderr)
        return 1
    infer_cli = repo_root / "tools" / "infer_cli.py"
    modules = repo_root / "infer" / "modules" / "vc" / "modules.py"
    pipeline = repo_root / "infer" / "modules" / "vc" / "pipeline.py"

    missing = [str(path) for path in (infer_cli, modules, pipeline) if not path.exists()]
    if missing:
        print("Missing expected RVC files:", file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        return 1

    if args.check:
        infer_text = infer_cli.read_text(encoding="utf-8")
        modules_text = modules.read_text(encoding="utf-8")
        pipeline_text = pipeline.read_text(encoding="utf-8")
        ok = (
            '--formant", type=float' in infer_text
            and "args.formant" in infer_text
            and "formant=0.0" in modules_text
            and "formant=0.0" in pipeline_text
            and "f0_up_key - formant" in pipeline_text
        )
        print("patched" if ok else "not-patched")
        return 0 if ok else 1

    changed = False
    changed = _patch_infer_cli(infer_cli) or changed
    changed = _patch_modules(modules) or changed
    changed = _patch_pipeline(pipeline) or changed
    print("patched" if changed else "already-patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
