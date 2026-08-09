import imghdr
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_DECODER_ENABLED = True
except Exception:
    HEIF_DECODER_ENABLED = False

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_LONGEST_SIDE = 5000
ALLOWED_TYPES = {"jpeg", "png", "heic"}
HEIC_BRANDS = {
    b"heic",
    b"heix",
    b"hevc",
    b"hevx",
    b"heim",
    b"heis",
    b"mif1",
    b"msf1",
}


def _invalid_image(reason: str) -> HTTPException:
    return HTTPException(status_code=400, detail=reason)


def _detect_heic(image_bytes: bytes) -> bool:
    if len(image_bytes) < 12:
        return False
    if image_bytes[4:8] != b"ftyp":
        return False
    return image_bytes[8:12] in HEIC_BRANDS


def _detect_image_type(image_bytes: bytes) -> str | None:
    kind = imghdr.what(None, h=image_bytes[:512])
    if kind in {"jpeg", "png"}:
        return kind
    if _detect_heic(image_bytes):
        return "heic"
    return None


def _validate_dimensions(img: Image.Image) -> None:
    if img.width <= 0 or img.height <= 0:
        raise _invalid_image("file is not valid image: invalid dimensions")
    if max(img.width, img.height) > MAX_LONGEST_SIDE:
        raise _invalid_image(
            f"file is not valid image: longest side exceeds {MAX_LONGEST_SIDE}px"
        )


def verify_and_reencode(image_bytes: bytes) -> bytes:
    if not image_bytes:
        raise _invalid_image("file is not valid image: empty upload")

    if len(image_bytes) > MAX_FILE_BYTES:
        raise _invalid_image("file is not valid image: file size exceeds 10MB")

    image_type = _detect_image_type(image_bytes)
    if image_type not in ALLOWED_TYPES:
        raise _invalid_image(
            "file is not valid image: only jpg/jpeg, png, and heic are allowed"
        )

    if image_type == "heic" and not HEIF_DECODER_ENABLED:
        raise _invalid_image("file is not valid image: HEIC decoder is unavailable")

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img.load()

            # For multi-frame inputs, normalize by using the first frame.
            frame_count = int(getattr(img, "n_frames", 1) or 1)
            if frame_count > 1:
                img.seek(0)

            _validate_dimensions(img)

            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")

            if img.height != 0:
                new_width = int(round(img.width * (1280 / img.height)))
                img = img.resize((new_width, 1280), resample=Image.LANCZOS)

            out = BytesIO()
            img.save(out, format="JPEG", quality=90, optimize=True)
            return out.getvalue()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"file is not valid image: unreadable or corrupted image data ({type(exc).__name__})",
        ) from exc
