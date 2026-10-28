"""Deterministic rules for the assembly-drawing kit completeness check.

The model/OCR layer is responsible for reading what is printed on a drawing.
Everything in this module is intentionally pure and deterministic: note
exemption, filename normalisation and the final exists/missing decision must be
reproducible without a model.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


# Drawing formats that the current upload pipeline can actually inspect.  A
# SolidWorks model is not a drawing, and native .slddrw files are not supported
# by the first version of the importer either.
DRAWING_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MODEL_SUFFIXES = {".sldprt", ".sldasm"}
NATIVE_DRAWING_SUFFIXES = {".slddrw", ".dwg", ".dxf"}
_KNOWN_SUFFIXES = DRAWING_SUFFIXES | MODEL_SUFFIXES | NATIVE_DRAWING_SUFFIXES

CHECK_SCOPE_NOTE = (
    "本次仅核对当前导入集合中的 PDF / PNG / JPG / TIFF / BMP / WebP 工程图；"
    "SLDPRT、SLDASM 模型文件以及未导入的 SLDDRW/DWG/DXF 不参与匹配。"
)

_CODE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{2,39}$")


def strip_code_boundary(value: object) -> str:
    """Canonicalise a code before validation/comparison.

    OCR tables and filenames often surround a code with punctuation, a trailing
    separator, or the file suffix.  Remove those boundary artifacts once, but
    keep internal dots, leading zeroes and separators.
    """
    text = clean_text(value).strip(_BOUNDARY_PUNCTUATION)
    lowered = text.lower()
    for suffix in sorted(_KNOWN_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip(_BOUNDARY_PUNCTUATION).rstrip("._-/ ").strip()


def is_valid_code(value: object) -> bool:
    """A code must be code-like, contain both a letter and a digit.

    This rejects OCR noise such as ``$$`` or ``SSS`` while allowing dotted,
    hyphenated and slash-separated drawing numbers.
    """
    text = strip_code_boundary(value)
    if not _CODE_VALUE.fullmatch(text):
        return False
    return bool(re.search(r"[A-Za-z]", text) and re.search(r"\d", text))


STATUS_LABELS = {
    "exists": "存在",
    "missing": "不存在",
    "candidate": "待复核",
    "unchecked": "未检查",
    "exempt": "免查",
}


def clean_text(value: object) -> str:
    """Normalise display text without discarding meaningful punctuation.

    NFKC handles full-width ASCII, full-width punctuation and full-width
    spaces.  We only collapse whitespace, never remove dots, leading zeroes or
    separators such as ``-`` and ``_``.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


_BOUNDARY_PUNCTUATION = ".,;:，。；：()（）[]【】"


def normalize_code(value: object) -> str:
    """Return the comparison form of a part/drawing code.

    Latin letters are case-folded because drawing files are frequently saved
    with upper/lower-case differences.  Dots, leading zeroes and the remaining
    characters are preserved, but OCR/table boundary punctuation around a code
    is removed consistently for validation and filename matching.
    """
    return strip_code_boundary(value).upper()


def normalize_name(value: object) -> str:
    return clean_text(value)


def split_filename(filename: str) -> tuple[str, str]:
    """Split the last extension only.

    ``SK.CNC.0003-外框.pdf`` -> ``("SK.CNC.0003-外框", ".pdf")``.
    A leading dot (a hidden file) is not treated as an extension.
    """
    name = clean_text(filename)
    if "." not in name:
        return name, ""
    stem, suffix = name.rsplit(".", 1)
    if not stem or not suffix or any(char.isspace() for char in suffix):
        return name, ""
    return stem, f".{suffix.lower()}"


def is_temp_filename(filename: str) -> bool:
    base = Path(clean_text(filename)).name
    return base.startswith("~$") or base.startswith("~")


@dataclass(frozen=True)
class DrawingFile:
    """A file in the selected import set, in display and matching forms."""

    drawing_id: int
    original_filename: str
    stored_filename: str = ""
    file_type: str = ""
    title: str = ""

    def public(self) -> dict:
        return {
            "drawing_id": int(self.drawing_id),
            "original_filename": self.original_filename,
            "stored_filename": self.stored_filename,
            "file_type": self.file_type,
            "title": self.title,
        }


def _file_sort_key(item: dict) -> tuple:
    return (item.get("drawing_id") or 0, item.get("original_filename") or "")


def match_files(code: object, name: object, files: Iterable[DrawingFile]) -> dict:
    """Match one BOM row against the files in the *selected* import set.

    Only four complete stem forms are accepted:

    * ``code``
    * ``code-name``
    * ``code_name``
    * ``code name``

    Matching is an exact comparison after NFKC/whitespace/case normalisation.
    Substring and fuzzy matches are deliberately not used.
    """
    normalized_code = normalize_code(code)
    normalized_name = normalize_name(name)
    if not normalized_code:
        return {
            "status": "unchecked",
            "matches": [],
            "candidates": [],
            "ignored": [],
            "reason": "代号缺失，无法进行文件匹配",
        }

    code_cmp = normalized_code.casefold()
    name_cmp = normalized_name.casefold()
    exact: list[dict] = []
    candidates: list[dict] = []
    ignored: list[dict] = []

    for item in files:
        entry = item.public() if isinstance(item, DrawingFile) else dict(item)
        original = clean_text(entry.get("original_filename") or entry.get("filename") or "")
        if not original:
            continue
        if is_temp_filename(original):
            ignored.append({**entry, "reason": "临时文件"})
            continue
        stem, suffix = split_filename(original)
        if suffix not in DRAWING_SUFFIXES:
            if suffix in MODEL_SUFFIXES:
                reason = "模型文件，不作为工程图"
            elif suffix in NATIVE_DRAWING_SUFFIXES:
                reason = "SolidWorks/DWG 原生图纸未导入，当前版本不参与匹配"
            else:
                reason = "非工程图文件"
            ignored.append({**entry, "reason": reason})
            continue

        stem_cmp = clean_text(stem).casefold()
        matched_form = None
        if stem_cmp == code_cmp:
            matched_form = "code"
        elif name_cmp:
            for separator in ("-", "_", " "):
                if stem_cmp == f"{code_cmp}{separator}{name_cmp}":
                    matched_form = f"code{separator}name"
                    break
        if matched_form:
            exact.append({**entry, "match_form": matched_form, "matched_filename": original})
            continue

        # A file with the same code but a different trailing name is a useful
        # candidate hint, never an automatic match.
        for separator in ("-", "_", " "):
            prefix = f"{code_cmp}{separator}"
            if stem_cmp.startswith(prefix) and len(stem_cmp) > len(prefix):
                candidates.append({
                    **entry,
                    "reason": "代号相同但名称不一致",
                    "matched_filename": original,
                })
                break

    exact.sort(key=_file_sort_key)
    candidates.sort(key=_file_sort_key)
    ignored.sort(key=_file_sort_key)
    if exact:
        status = "exists"
        reason = "匹配到完整代号/代号-名称文件名" + ("；另有候选文件" if candidates else "")
    elif candidates:
        status = "candidate"
        reason = "只找到同代号但名称不一致的文件，需人工复核"
    else:
        status = "missing"
        reason = "本次导入集合中没有符合命名规则的文件"
    return {
        "status": status,
        "matches": exact,
        "candidates": candidates,
        "ignored": ignored,
        "reason": reason,
        "checked_code": normalized_code,
        "checked_name": normalized_name,
    }


_EXEMPT_VALUES = ("标准件", "外购件", "无图")
_EXEMPT_SET = set(_EXEMPT_VALUES)
_LABEL_PREFIX = re.compile(r"^(?:备注|类型|说明|材料类型|物料类型)\s*[:：]\s*")
_WRAPPERS = (("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"), ("{", "}"), ("《", "》"))


def _strip_wrappers(value: str) -> str:
    text = value.strip()
    changed = True
    while changed and text:
        changed = False
        for left, right in _WRAPPERS:
            if len(text) >= 2 and text.startswith(left) and text.endswith(right):
                text = text[1:-1].strip()
                changed = True
                break
    return text


def classify_note(note: object) -> dict:
    """Classify a BOM note against the three explicit exemption values.

    Only an explicit, whole-value match is exempt.  ``非标准件`` and other
    compound notes are retained for manual review instead of being swallowed by
    a substring test.
    """
    raw = clean_text(note)
    if not raw:
        return {"exempt": False, "reason": None, "review": False, "normalized": ""}

    normalized = raw
    for _ in range(3):
        changed = _strip_wrappers(normalized)
        changed = _LABEL_PREFIX.sub("", changed).strip()
        if changed == normalized:
            break
        normalized = changed
    normalized = _strip_wrappers(normalized)
    normalized = re.sub(r"[。；;，,、/|\s]+$", "", normalized).strip()

    if normalized in _EXEMPT_SET:
        return {"exempt": True, "reason": normalized, "review": False, "normalized": normalized}

    # Explicitly separated tags such as ``标准件、外购件`` are all exempt.
    tokens = [part.strip() for part in re.split(r"[；;，,、/|\s]+", normalized) if part.strip()]
    if len(tokens) > 1:
        cleaned_tokens = [_strip_wrappers(_LABEL_PREFIX.sub("", token).strip()) for token in tokens]
        if all(token in _EXEMPT_SET for token in cleaned_tokens):
            reason = "+".join(dict.fromkeys(cleaned_tokens))
            return {"exempt": True, "reason": reason, "review": False, "normalized": normalized}

    # Any ambiguous note that contains an exemption word must be reviewed, but
    # is not exempt (e.g. 非标准件, 标准件+表面处理, 无图纸).
    if any(word in normalized for word in _EXEMPT_VALUES):
        return {
            "exempt": False,
            "reason": None,
            "review": True,
            "normalized": normalized,
            "review_reason": "备注含豁免关键词但语义不明确，需人工确认",
        }
    return {"exempt": False, "reason": None, "review": False, "normalized": normalized}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status or "未知")


def candidate_display(candidates: Iterable[dict]) -> str:
    return "；".join(
        str(item.get("matched_filename") or item.get("original_filename") or "")
        for item in candidates
        if item
    )
