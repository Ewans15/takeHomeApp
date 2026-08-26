"""
utils.py
Normalization and comparison helpers used across the verifier.
Deliberately dependency-free (stdlib only) so the app has no external
network/package requirements beyond OCR + image libs.
"""
import re
import unicodedata
from difflib import SequenceMatcher

# Text normalizatio

# Characters that should collapse to a plain ASCII apostrophe before comparing.
_APOSTROPHE_VARIANTS = "\u2018\u2019\u02bc\u00b4`"


def normalize_basic(text: str) -> str:
    """Lowercase, collapse whitespace, unify quote/dash characters.

    This is the level of normalization used for *fuzzy* fields (brand name,
    class/type) where formatting/case differences are expected and should
    NOT count as a mismatch -- e.g. "STONE'S THROW" on a label vs
    "Stone's Throw" typed into a form are the same brand.
    """
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", text)
    for ch in _APOSTROPHE_VARIANTS:
        text = text.replace(ch, "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.lower()
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_whitespace_only(text: str) -> str:
    """Collapse whitespace/line-breaks but preserve case and punctuation.

    Used for the Government Warning exact-text check, where case and
    wording matter but OCR line-wrapping should not cause a false mismatch.
    """
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", text)
    for ch in _APOSTROPHE_VARIANTS:
        text = text.replace(ch, "'")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    """Ratio in [0, 1] of how similar two already-normalized strings are."""
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()



# Field-specific extraction

_ABV_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_PROOF_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*proof", re.IGNORECASE)

# number + unit, e.g. "750 mL", "1.75 L", "750ML", "25.4 fl oz"
_VOLUME_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(m\s?l|milliliters?|l|liters?|litres?|fl\.?\s?oz\.?|fluid\s+ounces?)",
    re.IGNORECASE,
)

_UNIT_TO_ML = {
    "ml": 1.0,
    "l": 1000.0,
    "floz": 29.5735,
}


def _canon_unit(raw_unit: str) -> str:
    u = raw_unit.lower().replace(".", "").replace(" ", "")
    if u.startswith("ml") or "milliliter" in u:
        return "ml"
    if u.startswith("l") or "liter" in u or "litre" in u:
        return "l"
    if "floz" in u or "fluidounce" in u:
        return "floz"
    return u


def extract_abv_proof(text: str):
    """Return (abv_percent: float|None, proof: float|None) found in text."""
    abv_match = _ABV_RE.search(text or "")
    proof_match = _PROOF_RE.search(text or "")
    abv = float(abv_match.group(1)) if abv_match else None
    proof = float(proof_match.group(1)) if proof_match else None
    return abv, proof


def extract_volume_ml(text: str):
    """Return (value_in_ml: float|None, original_value: str|None, unit: str|None)."""
    m = _VOLUME_RE.search(text or "")
    if not m:
        return None, None, None
    value = float(m.group(1))
    unit = _canon_unit(m.group(2))
    factor = _UNIT_TO_ML.get(unit)
    ml = value * factor if factor else None
    return ml, m.group(1), unit
