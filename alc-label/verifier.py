"""
verifier.py
Compares submitted form data against OCR'd label text, field by field,
and returns a structured result the UI (and the batch CSV export) can
render directly.
"""
from __future__ import annotations

import time

import ocr_engine
import utils

STANDARD_GOVERNMENT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to "
    "drive a car or operate machinery, and may cause health problems."
)

FUZZY_PASS_THRESHOLD = 0.90
FUZZY_REVIEW_THRESHOLD = 0.75


def _verdict_from_ratio(ratio: float) -> str:
    if ratio >= FUZZY_PASS_THRESHOLD:
        return "pass"
    if ratio >= FUZZY_REVIEW_THRESHOLD:
        return "review"
    return "fail"


def check_fuzzy_field(field_label: str, form_value: str, label_text: str) -> dict:
    """Brand Name / Class-Type style check: case/format-insensitive."""
    a = utils.normalize_basic(form_value)
    b = utils.normalize_basic(label_text)
    ratio = _best_substring_ratio(a, b)
    return {
        "field": field_label,
        "form_value": form_value,
        "matched_ratio": round(ratio, 2),
        "verdict": _verdict_from_ratio(ratio),
        "detail": f"{ratio*100:.0f}% text similarity after case/punctuation normalization.",
    }


def _best_substring_ratio(needle: str, haystack: str) -> float:
    """Slide a needle-length window across haystack and keep the best ratio.
    Cheap way to find 'OLD TOM DISTILLERY' inside a full page of OCR text
    without needing a real layout-aware search."""
    if not needle:
        return 1.0 if not haystack else 0.0
    if not haystack:
        return 0.0
    words = haystack.split(" ")
    n_words = max(1, len(needle.split(" ")))
    best = 0.0
    # try windows of n_words, n_words+1, n_words-1 for slight length drift
    for width in (n_words, n_words + 1, max(1, n_words - 1)):
        for i in range(0, max(1, len(words) - width + 1)):
            window = " ".join(words[i:i + width])
            r = utils.similarity(needle, window)
            if r > best:
                best = r
    # also compare against the whole haystack in case it's already short
    best = max(best, utils.similarity(needle, haystack))
    return best


def check_alcohol_content(form_value: str, label_text: str) -> dict:
    form_abv, form_proof = utils.extract_abv_proof(form_value)
    label_abv, label_proof = utils.extract_abv_proof(label_text)

    issues = []
    if form_abv is None:
        issues.append("could not parse a %ABV from the form value")
    if label_abv is None:
        issues.append("could not find a %ABV on the label")

    abv_match = form_abv is not None and label_abv is not None and abs(form_abv - label_abv) < 0.05
    proof_match = True
    proof_note = None
    if form_proof is not None or label_proof is not None:
        proof_match = (
            form_proof is not None and label_proof is not None and abs(form_proof - label_proof) < 0.5
        )
        if not proof_match:
            proof_note = f"form proof={form_proof}, label proof={label_proof}"

    verdict = "pass" if (abv_match and proof_match and not issues) else ("fail" if (form_abv and label_abv) else "review")

    consistency_note = None
    if form_abv is not None and form_proof is not None and abs(form_proof - form_abv * 2) > 0.5:
        consistency_note = "Note: form's proof is not 2x its %ABV -- worth a manual double-check."

    return {
        "field": "Alcohol Content",
        "form_value": form_value,
        "verdict": verdict,
        "detail": f"form: {form_abv}% / {form_proof} proof  vs.  label (OCR): {label_abv}% / {label_proof} proof."
        + (f" {proof_note}." if proof_note else "")
        + (f" {consistency_note}" if consistency_note else "")
        + (f" [{'; '.join(issues)}]" if issues else ""),
    }


def check_net_contents(form_value: str, label_text: str) -> dict:
    form_ml, form_raw, form_unit = utils.extract_volume_ml(form_value)
    label_ml, label_raw, label_unit = utils.extract_volume_ml(label_text)

    if form_ml is None or label_ml is None:
        return {
            "field": "Net Contents",
            "form_value": form_value,
            "verdict": "review",
            "detail": "Could not confidently parse a volume from the form and/or the label OCR text -- verify manually.",
        }

    match = abs(form_ml - label_ml) < 0.5  
    return {
        "field": "Net Contents",
        "form_value": form_value,
        "verdict": "pass" if match else "fail",
        "detail": f"form: {form_raw} {form_unit}  vs.  label (OCR): {label_raw} {label_unit}.",
    }


def check_government_warning(form_value: str, ocr_result) -> dict:
    label_text = ocr_result.raw_text
    canonical = form_value.strip() if form_value.strip() else STANDARD_GOVERNMENT_WARNING

    norm_canonical = utils.normalize_whitespace_only(canonical)
    norm_label = utils.normalize_whitespace_only(label_text)

    exact_match = norm_canonical in norm_label
    close_ratio = utils.similarity(norm_canonical, norm_label[:len(norm_canonical) + 40]) if not exact_match else 1.0

    caps_ok = "GOVERNMENT WARNING:" in label_text  # case-sensitive on purpose

    phrase_box = ocr_engine.find_phrase_boxes(ocr_result, ["GOVERNMENT", "WARNING:"])
    if phrase_box is None:
        phrase_box = ocr_engine.find_phrase_boxes(ocr_result, ["GOVERNMENT", "WARNING"])
    bold_result = ocr_engine.estimate_bold(ocr_result, phrase_box)

    if exact_match and caps_ok and bold_result.get("likely_bold"):
        verdict = "pass"
    elif exact_match and caps_ok:
        verdict = "review"  # text/caps correct, bold unconfirmed
    else:
        verdict = "fail"

    detail_bits = []
    detail_bits.append("Exact wording: MATCH" if exact_match else f"Exact wording: NO MATCH (similarity {close_ratio*100:.0f}%)")
    detail_bits.append("'GOVERNMENT WARNING:' in all caps: YES" if caps_ok else "'GOVERNMENT WARNING:' in all caps: NOT FOUND as written")
    if bold_result["checked"]:
        detail_bits.append(
            f"Bold heuristic: {'likely bold' if bold_result['likely_bold'] else 'not detected as bold'} "
            f"(ink-density ratio {bold_result['density_ratio']}x vs. body text -- best effort, verify visually)"
        )
    else:
        detail_bits.append(f"Bold heuristic: unable to check ({bold_result['reason']}) -- verify visually")

    return {
        "field": "Government Warning",
        "form_value": "[standard text]" if not form_value.strip() else form_value,
        "verdict": verdict,
        "detail": " | ".join(detail_bits),
    }


def verify_label(form_data: dict, file_bytes) -> dict:
    """form_data keys: brand_name, class_type, alcohol_content, net_contents,
    government_warning (optional -- defaults to the standard statement)."""
    start = time.time()
    ocr_result = ocr_engine.run_ocr(file_bytes)
    label_text = ocr_result.raw_text

    results = [
        check_fuzzy_field("Brand Name", form_data.get("brand_name", ""), label_text),
        check_fuzzy_field("Class/Type", form_data.get("class_type", ""), label_text),
        check_alcohol_content(form_data.get("alcohol_content", ""), label_text),
        check_net_contents(form_data.get("net_contents", ""), label_text),
        check_government_warning(form_data.get("government_warning", ""), ocr_result),
    ]

    overall = "pass"
    if any(r["verdict"] == "fail" for r in results):
        overall = "fail"
    elif any(r["verdict"] == "review" for r in results):
        overall = "review"

    elapsed = round(time.time() - start, 2)
    return {
        "overall": overall,
        "elapsed_seconds": elapsed,
        "fields": results,
        "raw_ocr_text": label_text.strip(),
    }
