"""Private bounded image snapshots for the inference pipe."""
from contextlib import contextmanager
import hashlib
import os
import stat
from uuid import uuid4

from ..config import settings
from ..llm.base import LLMImageInput
from .image_protocol import validate_manifest


def image_root():
    root = settings.data_dir / "runtime/model-images"
    if root.is_symlink():
        raise ValueError("invalid image root")
    return root


@contextmanager
def staged_images(images):
    if type(images) not in {list, tuple} or len(images) > 4:
        raise ValueError("invalid images")
    entries = []
    for item in images:
        if not isinstance(item, LLMImageInput) or type(item.data) is not bytes:
            raise ValueError("invalid image")
        if not 0 < len(item.data) <= min(settings.vision_max_image_bytes, 8000000):
            raise ValueError("invalid image size")
        entries.append(dict(id=uuid4().hex, size=len(item.data),
                            sha256=hashlib.sha256(item.data).hexdigest(), media_type=item.media_type))
    entries = validate_manifest(entries, settings.vision_max_image_bytes)
    paths = []
    try:
        if entries:
            root = image_root()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for entry, item in zip(entries, images):
                path = root / (entry["id"] + ".bin")
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                paths.append(path)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(item.data)
        yield entries
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def load_images(manifest):
    entries = validate_manifest(manifest, settings.vision_max_image_bytes)
    result = []
    for entry in entries:
        path = image_root() / (entry["id"] + ".bin")
        if path.is_symlink():
            raise ValueError("invalid image snapshot")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != entry["size"]:
                raise ValueError("invalid image snapshot")
            data = source.read(entry["size"] + 1)
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("invalid image snapshot")
        result.append(LLMImageInput(data=data, media_type=entry["media_type"]))
    return result
