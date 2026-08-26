"""
ocr_engine.py
All image -> text work lives here. Uses Tesseract (local, offline, via
pytesseract) 
Requires the *tesseract-ocr* system binary to be installed which can be done by Docker
"""
from __future__ import annotations

import cv2
import numpy as np
import pytesseract
from PIL import Image

MAX_DIMENSION = 2200  


def _load_image(file_bytes: bytes) -> np.ndarray:
    pil_img = Image.open(file_bytes) if hasattr(file_bytes, "read") else Image.open(
        __import__("io").BytesIO(file_bytes)
    )
    pil_img = pil_img.convert("RGB")
    arr = np.array(pil_img)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _resize_if_needed(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= MAX_DIMENSION:
        return img
    scale = MAX_DIMENSION / longest
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _preprocess(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )
    return thresh


class OcrResult:
    def __init__(self, raw_text: str, data: dict, processed_image: np.ndarray):
        self.raw_text = raw_text
        self.data = data  
        self.processed_image = processed_image


def run_ocr(file_bytes) -> OcrResult:
    """Extract text (and word-level layout data) from a label image."""
    img = _load_image(file_bytes)
    img = _resize_if_needed(img)
    processed = _preprocess(img)

    config = "--oem 3 --psm 3"
    raw_text = pytesseract.image_to_string(processed, config=config)
    data = pytesseract.image_to_data(
        processed, config=config, output_type=pytesseract.Output.DICT
    )
    return OcrResult(raw_text=raw_text, data=data, processed_image=processed)


def find_phrase_boxes(ocr_result: OcrResult, phrase_words: list[str]):
    """Locate consecutive OCR word tokens that spell out `phrase_words`
    (case-insensitive) and return their combined bounding box, or None.
    Used to crop the 'GOVERNMENT WARNING:' region for the bold heuristic.
    """
    words = ocr_result.data["text"]
    n = len(words)
    target = [w.lower().strip(":,.") for w in phrase_words]
    for i in range(n - len(target) + 1):
        window = [words[i + j].lower().strip(":,.") for j in range(len(target))]
        if window == target:
            xs, ys, xe, ye = [], [], [], []
            for j in range(len(target)):
                idx = i + j
                x, y = ocr_result.data["left"][idx], ocr_result.data["top"][idx]
                w, h = ocr_result.data["width"][idx], ocr_result.data["height"][idx]
                xs.append(x)
                ys.append(y)
                xe.append(x + w)
                ye.append(y + h)
            return min(xs), min(ys), max(xe), max(ye)
    return None


def _ink_density(gray_region: np.ndarray) -> float:
    """Fraction of dark (ink) pixels in a binarized region. Higher density
    generally correlates with heavier / bolder strokes for the same font."""
    if gray_region.size == 0:
        return 0.0
    dark = np.sum(gray_region < 128)
    return dark / gray_region.size


def estimate_bold(ocr_result: OcrResult, phrase_box, paragraph_box=None) -> dict:
    """Best-effort heuristic: compare ink density of the phrase region to
    the surrounding warning paragraph. OCR engines don't expose font-weight
    directly, so this is a signal, not a certainty -- always surfaced to the
    user as 'best effort, verify visually' rather than a hard pass/fail.
    """
    if phrase_box is None:
        return {"checked": False, "likely_bold": False, "reason": "phrase not located in image"}

    x0, y0, x1, y1 = phrase_box
    img = ocr_result.processed_image
    pad = 2
    phrase_region = img[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    phrase_density = _ink_density(phrase_region)

    if paragraph_box:
        px0, py0, px1, py1 = paragraph_box
        para_region = img[py0:py1, px0:px1]
    else:
        # fall back to a horizontal strip below the phrase as "body text"
        strip_h = (y1 - y0) * 3
        para_region = img[y1:y1 + strip_h, :]
    body_density = _ink_density(para_region)

    if body_density <= 0:
        return {"checked": False, "likely_bold": False, "reason": "insufficient body text to compare"}

    ratio = phrase_density / body_density
    return {
        "checked": True,
        "likely_bold": ratio >= 1.15,
        "density_ratio": round(ratio, 2),
        "reason": None,
    }
