import re
import warnings
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from api.config import Settings
from api.errors import ServiceError


def _number(header: str, name: str) -> float | None:
    match = re.search(rf'{name}\s*=\s*["\x27]([^"\x27]+)', header)
    try:
        value = float(match.group(1)) if match else None
        return value if value is not None and isfinite(value) else None
    except ValueError:
        return None


def _gps(exif: Image.Exif) -> list[float] | None:
    try:
        values = exif.get_ifd(34853)
        latitude = sum(float(v) / divisor for v, divisor in zip(values[2], (1, 60, 3600)))
        longitude = sum(float(v) / divisor for v, divisor in zip(values[4], (1, 60, 3600)))
        latitude *= -1 if values[1] == "S" else 1
        longitude *= -1 if values[3] == "W" else 1
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            return [latitude, longitude]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def inspect_image(path: Path, settings: Settings) -> dict:
    """Read camera metadata and verify the actual image format, not its extension."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in {"JPEG", "MPO", "PNG"}:
                    raise ServiceError(415, "UNSUPPORTED_FORMAT", "Допустимы только JPG и PNG")
                if image.width * image.height > settings.max_pixels:
                    raise ServiceError(413, "IMAGE_TOO_LARGE", "Слишком большое разрешение снимка")
                container_format = image.format
                image_format = "JPEG" if container_format == "MPO" else container_format
                frame_count = getattr(image, "n_frames", 1)
                width, height = image.size
                exif = image.getexif()
                try:
                    details = exif.get_ifd(34665)
                except (KeyError, TypeError, ValueError):
                    details = {}
                timestamp = details.get(36867) or exif.get(306)
                taken_at = None
                if timestamp:
                    try:
                        local = datetime.strptime(str(timestamp), "%Y:%m:%d %H:%M:%S")
                        offset_text = str(details.get(36881, ""))
                        offset = timezone(timedelta(hours=settings.photo_utc_offset_hours))
                        if re.fullmatch(r"[+-]\d{2}:\d{2}", offset_text):
                            sign = -1 if offset_text[0] == "-" else 1
                            offset = timezone(
                                sign
                                * timedelta(
                                    hours=int(offset_text[1:3]), minutes=int(offset_text[4:6])
                                )
                            )
                        taken_at = local.replace(tzinfo=offset).isoformat()
                    except (ValueError, OverflowError):
                        pass
                gps = _gps(exif)
                if exif.get(274) in {5, 6, 7, 8}:
                    width, height = height, width
            # PNG metadata access may load the file; verify a freshly opened stream.
            with Image.open(path) as verified:
                verified.verify()
    except ServiceError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ServiceError(415, "INVALID_IMAGE", "Файл повреждён или не является снимком") from exc

    with path.open("rb") as stream:
        header = stream.read(262144).decode("utf-8", errors="ignore")
    pitch = _number(header, "GimbalPitchDegree")
    return {
        "width": width,
        "height": height,
        "format": image_format,
        "container_format": container_format,
        "frame_count": frame_count,
        "gps": gps,
        "gps_source": "exif" if gps else None,
        "taken_at": taken_at,
        "has_exif": bool(exif),
        "camera_pitch": pitch,
        "camera_yaw": _number(header, "GimbalYawDegree"),
        "camera_roll": _number(header, "GimbalRollDegree"),
        "relative_altitude": _number(header, "RelativeAltitude"),
        "absolute_altitude": _number(header, "AbsoluteAltitude"),
        "calibrated_focal_length_px": _number(header, "CalibratedFocalLength"),
        "optical_center_x_px": _number(header, "CalibratedOpticalCenterX"),
        "optical_center_y_px": _number(header, "CalibratedOpticalCenterY"),
        "rtk_flag": _number(header, "RtkFlag"),
        "is_overhead": pitch is not None and pitch <= -75,
        "view": None,
        "view_source": None,
    }


def load_oriented_image(path: Path) -> Image.Image:
    with Image.open(path) as source:
        source.seek(0)
        return ImageOps.exif_transpose(source).convert("RGB")


def save_preview(image: Image.Image, target: Path, size: int = 512) -> None:
    preview = ImageOps.contain(image, (size, size))
    preview.save(target, "JPEG", quality=85)


def save_crop(image: Image.Image, bbox: tuple, target: Path) -> None:
    x1, y1, x2, y2 = bbox
    left, top = int(x1 * image.width), int(y1 * image.height)
    right = min(image.width, max(left + 1, int(x2 * image.width)))
    bottom = min(image.height, max(top + 1, int(y2 * image.height)))
    crop = image.crop((left, top, right, bottom))
    save_preview(crop, target, size=1024)
