"""Sniff, clean and describe uploaded files (spec 04)."""

from __future__ import annotations

import hashlib
import io
import posixpath
from dataclasses import dataclass

import filetype
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from slugify import slugify

from .config import Media

# by MIME type: (Pillow format, extension)
_IMAGE_FORMATS = {
    "image/jpeg": ("JPEG", "jpg"),
    "image/png": ("PNG", "png"),
    "image/webp": ("WEBP", "webp"),
}
# Refuse images that claim more pixels than this, before decoding them: a small file can
# declare a canvas that takes gigabytes to decode (a decompression bomb). Real photos from
# the biggest phone sensors are about 200 MP.
MAX_PIXELS = 200_000_000
_MEDIA_TYPES = {"image": "photo", "video": "video", "audio": "audio"}


class MediaError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Processed:
    content: bytes
    filename: str
    ext: str
    media_type: str  # photo | video | audio
    content_type: str
    md5: str


def _open_image(data: bytes) -> Image.Image:
    """Open and fully decode an image, refusing one with too many pixels before decoding."""
    try:
        im = Image.open(io.BytesIO(data))  # reads the header only
        if im.width * im.height > MAX_PIXELS:
            raise MediaError(f"The image has more than {MAX_PIXELS:,} pixels", 413)
        im.load()
    except Image.DecompressionBombError as exc:
        raise MediaError(f"The image has more than {MAX_PIXELS:,} pixels", 413) from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise MediaError("The image could not be read", 400) from exc
    return im


def _clean_gif(data: bytes) -> bytes:
    """Re-encode a GIF so its comment and other extensions are dropped, keeping the animation."""
    im = _open_image(data)
    frames, durations = [], []
    for frame in ImageSequence.Iterator(im):
        frames.append(frame.copy())
        durations.append(frame.info.get("duration", 100))
    out = io.BytesIO()
    options = {"duration": durations}
    if "loop" in im.info:
        options["loop"] = im.info["loop"]
    # An empty comment stops Pillow carrying the original one over.
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], comment=b"", **options)
    return out.getvalue()


def _clean_image(data: bytes, mime: str, media: Media) -> bytes:
    im = _open_image(data)
    pil_format, _ = _IMAGE_FORMATS[mime]
    im = ImageOps.exif_transpose(im)  # apply the camera's rotation before it is discarded
    im.thumbnail((media.image.max_width, media.image.max_height), Image.Resampling.LANCZOS)
    # Nothing from im.info or the EXIF block is passed to save(), so none of it is written.
    # A fresh image object, not the original, guarantees no metadata rides along.
    clean = Image.new(im.mode, im.size)
    clean.paste(im)
    out = io.BytesIO()
    if pil_format == "JPEG":
        if clean.mode not in ("RGB", "L"):
            clean = clean.convert("RGB")
        clean.save(out, "JPEG", quality=media.image.jpeg_quality, progressive=True, optimize=True)
    elif pil_format == "PNG":
        clean.save(out, "PNG", optimize=True)
    else:
        clean.save(out, "WEBP", quality=media.image.jpeg_quality)
    return out.getvalue()


def process_upload(data: bytes, original_name: str, media: Media) -> Processed:
    if len(data) > media.max_bytes:
        raise MediaError(f"The file is larger than {media.max_bytes} bytes", 413)
    kind = filetype.guess(data)
    if kind is None:
        raise MediaError("This type of file is not supported", 415)
    family = kind.mime.split("/", 1)[0]
    if family not in _MEDIA_TYPES:
        raise MediaError(f"{kind.mime} files are not supported", 415)

    ext, content_type, content = kind.extension, kind.mime, data
    if family == "image":
        if kind.mime in _IMAGE_FORMATS:
            content = _clean_image(data, kind.mime, media)
            ext = _IMAGE_FORMATS[kind.mime][1]
        elif kind.mime == "image/gif":
            content = _clean_gif(data)
        else:
            raise MediaError(f"{kind.mime} images are not supported", 415)

    stem = slugify(posixpath.splitext(posixpath.basename(original_name))[0]) or "file"
    return Processed(
        content=content,
        filename=f"{stem}.{ext}",
        ext=ext,
        media_type=_MEDIA_TYPES[family],
        content_type=content_type,
        md5=hashlib.md5(content).hexdigest(),  # noqa: S324 - a content name, not a security use
    )
