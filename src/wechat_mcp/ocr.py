"""Local OCR for WeChat's sender labels (macOS Vision framework).

WeChat draws group sender names as small text above incoming bubbles and does
not expose them through the Accessibility API. This module runs Apple's
on-device text recogniser over a small crop — nothing leaves the machine and no
file is written.
"""

from __future__ import annotations

import contextlib

from PIL import Image

try:  # pragma: no cover - import guard
    import Quartz
    import Vision
except Exception:  # pragma: no cover
    Quartz = None
    Vision = None


def available() -> bool:
    return Quartz is not None and Vision is not None


def recognize_text(image: Image.Image) -> str | None:
    """Return recognised text for a PIL image, or ``None``."""
    if not available() or image.width < 2 or image.height < 2:
        return None

    rgb = image.convert("RGBA")
    data = rgb.tobytes("raw", "RGBA")
    provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
    if provider is None:
        return None

    cg_image = Quartz.CGImageCreate(
        rgb.width,
        rgb.height,
        8,
        32,
        rgb.width * 4,
        Quartz.CGColorSpaceCreateDeviceRGB(),
        Quartz.kCGImageAlphaPremultipliedLast,
        provider,
        None,
        False,
        Quartz.kCGRenderingIntentDefault,
    )
    if cg_image is None:
        return None

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    with contextlib.suppress(Exception):  # pragma: no cover - older Vision
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        return None

    lines: list[str] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    text = " ".join(part.strip() for part in lines if part.strip()).strip()
    return text or None
