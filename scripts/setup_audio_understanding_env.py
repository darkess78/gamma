from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an isolated Gamma audio-understanding runtime.")
    parser.add_argument("--python", default="python3.12")
    parser.add_argument("--venv", default=".venv-audio")
    parser.add_argument("--torch-index-url", default="https://download.pytorch.org/whl/cu128")
    parser.add_argument("--cpu", action="store_true", help="Install CPU PyTorch instead of a CUDA wheel.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    venv_path = (repo_root / args.venv).resolve()
    python_path = venv_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    if not python_path.exists():
        _run([args.python, "-m", "venv", str(venv_path)], cwd=repo_root)

    _run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], cwd=repo_root)
    torch_command = [str(python_path), "-m", "pip", "install", "torch"]
    if not args.cpu:
        torch_command.extend(["--index-url", args.torch_index_url])
    _run(torch_command, cwd=repo_root)
    _run(
        [
            str(python_path),
            "-m",
            "pip",
            "install",
            "fastapi>=0.115.0",
            "uvicorn>=0.30.0",
            "pydantic>=2.8.0",
            "python-dotenv>=1.0.1",
            "python-multipart>=0.0.9",
            "httpx>=0.27.0",
            "transformers>=4.48.0",
            "safetensors>=0.4.5",
            "sympy>=1.13.0",
            "numpy>=1.26.0",
        ],
        cwd=repo_root,
    )
    _run([str(python_path), "-m", "pip", "install", "-e", ".", "--no-deps"], cwd=repo_root)
    _run(
        [
            str(python_path),
            "-c",
            (
                "import torch; "
                "print('torch', torch.__version__); "
                "print('cuda_build', torch.version.cuda); "
                "print('cuda_available', torch.cuda.is_available()); "
                "print('device_count', torch.cuda.device_count())"
            ),
        ],
        cwd=repo_root,
    )
    print(f"Audio-understanding environment ready: {python_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
