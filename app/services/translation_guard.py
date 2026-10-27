"""Engineering-token protection for machine translation.

The translator must never silently rewrite dimensions, tolerances, material
grades, standards, or drawing numbers. Tokens are replaced with stable
placeholders before the model sees the text and restored only after validation.
"""

from __future__ import annotations

import re

_TOKEN_PATTERNS = [
    re.compile(r"(?:⌀|Φ|φ|Ø)\s*\d+(?:\.\d+)?"),
    re.compile(r"±\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|in|°|%|HRC|HB|HV|Ra|μm|um)?"),
    re.compile(r"(?<![A-Za-z0-9])(?:Ra|Rz)\s*\d+(?:\.\d+)?(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:HRC|HB|HV)\s*\d+(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:M|G|R|SR|C)\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?(?![A-Za-z0-9])"),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*[～~\-—–]\s*\d+(?:\.\d+)?\s*(?:HBW|HRC|HB|HV|mm|cm|m|in|°|%|μm|um)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:mm|cm|m|in|°|%|HRC|HB|HV|μm|um)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN)\s*(?:/\s*T)?\s*[A-Z0-9][A-Z0-9.\-—–]*(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:Q\d+[A-Z]?|40Cr|20CrMnTi|45#|06Cr19Ni10|304|316L|6061|7075|HT200|QT\d+-\d+)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,8}(?:-[A-Z0-9]{1,12}){2,}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,8}\d{2,}(?:[.-]\d+)*(?![A-Za-z0-9])"),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*[×x]\s*\d+(?:\.\d+)?(?![\d.])"),
    re.compile(r"(?<![\d.])\d+:\d+(?![\d.])"),
    re.compile(r"(?<![A-Za-z0-9])A[0-4](?![A-Za-z0-9])"),
]

_PLACEHOLDER = "SCGPH{:04d}"
_UNIT = re.compile(r"\b(?:mm|cm|inch|inches)\b|(?<=\d)\s*in\b", re.I)


def _spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            if match.end() > match.start():
                spans.append((match.start(), match.end()))
    # Prefer the longest match when patterns overlap.
    spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            old_start, old_end = merged[-1]
            if end > old_end:
                merged[-1] = (old_start, end)
        else:
            merged.append((start, end))
    return merged


def protected_tokens(text: str) -> list[str]:
    return [text[start:end] for start, end in _spans(text or "")]


def protect(text: str) -> tuple[str, dict[str, str]]:
    source = text or ""
    spans = _spans(source)
    if not spans:
        return source, {}
    pieces: list[str] = []
    mapping: dict[str, str] = {}
    cursor = 0
    for index, (start, end) in enumerate(spans, 1):
        marker = _PLACEHOLDER.format(index)
        pieces.append(source[cursor:start])
        pieces.append(marker)
        mapping[marker] = source[start:end]
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces), mapping


def restore(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    restored = text or ""
    missing: list[str] = []
    for marker, original in mapping.items():
        if marker not in restored:
            missing.append(original)
            continue
        restored = restored.replace(marker, original)
    return restored, missing


def validate(source: str, translated: str) -> list[str]:
    issues: list[str] = []
    for token in protected_tokens(source):
        if token not in (translated or ""):
            issues.append(f"工程数据未原样保留：{token}")
    source_units = set(match.group(0).strip().lower() for match in _UNIT.finditer(source or ""))
    target_units = set(match.group(0).strip().lower() for match in _UNIT.finditer(translated or ""))
    if "mm" in source_units and "in" in target_units and "in" not in source_units:
        issues.append("译文疑似把毫米换算成了英寸")
    source_standards = set(re.findall(r"\b(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN)\b", source or "", flags=re.I))
    target_standards = set(re.findall(r"\b(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN)\b", translated or "", flags=re.I))
    if source_standards and any(item.upper() not in {value.upper() for value in source_standards} for item in target_standards):
        issues.append("译文疑似替换了标准体系")
    return issues
