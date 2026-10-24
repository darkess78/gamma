"""Download a pinned official trainable coding candidate without private tokens."""
from pathlib import Path
import hashlib
import json
import shutil

from .autonomous_store import write_json, safe_root

CHECKPOINT = 'Qwen/Qwen3-4B-Instruct-2507'


def main():
    from ..config import settings
    from huggingface_hub import HfApi, snapshot_download
    root = safe_root(settings.data_dir/'coding'/'models'/'qwen3-4b-instruct-2507')
    receipt = root/'download.json'
    if receipt.exists():
        raise RuntimeError('A recorded candidate download must be inspected before reuse')
    info = HfApi(token=False).model_info(CHECKPOINT, files_metadata=True)
    if info.gated or not info.sha or len(info.sha) != 40:
        raise RuntimeError('Official checkpoint identity unavailable')
    patterns = ['*.safetensors','*.json','*.txt','*.model','*.tiktoken']
    files = [f for f in info.siblings if f.rfilename.endswith(('.safetensors','.json','.txt','.model','.tiktoken'))]
    required = sum(f.size or 0 for f in files)
    if not required or shutil.disk_usage(root).free < required + 8_000_000_000:
        raise RuntimeError('Candidate requires more disk headroom')
    write_json(receipt, {'status':'downloading','checkpoint':CHECKPOINT,'revision':info.sha,
                         'required_bytes':required,'model_weights_changed':False})
    try:
        snapshot_download(CHECKPOINT, revision=info.sha, local_dir=root/'base',
                          allow_patterns=patterns, token=False, max_workers=2)
        hashes = {str(p.relative_to(root/'base')):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (root/'base').glob('*.safetensors')}
        if not hashes:raise RuntimeError('No trainable model weights were downloaded')
        write_json(receipt, {'status':'ready','checkpoint':CHECKPOINT,'revision':info.sha,
                             'required_bytes':required,'weight_hashes':hashes,
                             'model_weights_changed':False})
    except Exception as exc:
        write_json(receipt, {'status':'failed','checkpoint':CHECKPOINT,'revision':info.sha,
                             'error':type(exc).__name__,'model_weights_changed':False})
        raise


if __name__ == '__main__':main()
