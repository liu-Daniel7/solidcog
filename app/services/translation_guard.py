"""Engineering-token protection for machine translation.

The translator must never silently rewrite dimensions, tolerances, material
grades, standards, or drawing numbers. Tokens are replaced with stable
placeholders before the model sees the text and restored only after validation.
"""

from __future__ import annotations

import re

_UNIT_ALTS = r"mm|cm|m|in|inch|inches|kg|g|mg|t|MPa|kPa|Pa|N|kN|rpm|min|s|h|L|mL|ml|°C|℃|lb|lbs|psi"

_TOKEN_PATTERNS = [
    re.compile(r"(?:⌀|Φ|φ|Ø)\s*\d+(?:\.\d+)?"),
    re.compile(r"±\s*\d+(?:\.\d+)?\s*(?:" + _UNIT_ALTS + r")?"),
    re.compile(r"(?<![A-Za-z0-9])(?:Ra|Rz)\s*\d+(?:\.\d+)?(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:HRC|HRA|HRB|HBW|HBS|HB|HV)\s*\d+(?:\.\d+)?(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:HRC|HRA|HRB|HBW|HBS|HB|HV)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:M|G|R|SR|C)\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?(?![A-Za-z0-9])"),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*[～~\-—–]\s*\d+(?:\.\d+)?\s*(?:" + _UNIT_ALTS + r"|HRC|HRA|HRB|HBW|HBS|HB|HV)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:" + _UNIT_ALTS + r"|HRC|HRA|HRB|HBW|HBS|HB|HV)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*[+-]\s*\d+(?:\.\d+)?(?:\s*(?:" + _UNIT_ALTS + r"))?(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![\d.])[+-]\s*\d+(?:\.\d+)?(?![.\d])"),
    re.compile(r"(?<![A-Za-z0-9])(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN)\s*(?:/\s*T)?\s*[A-Z0-9][A-Z0-9.\-—–]*(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])(?:Q\d+[A-Z]?|\d{2,3}(?:Cr|Mn|Ni|Mo|Si|Ti|V|W|B|Al|Cu)[A-Za-z0-9]*|40Cr|20CrMnTi|45#|06Cr19Ni10|304|316L|6061|7075|HT200|QT\d+-\d+)(?![A-Za-z0-9])", re.I),
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,8}(?:-[A-Z0-9]{1,12}){2,}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,8}\d{2,}(?:[.-]\d+)*(?![A-Za-z0-9])"),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*[×x]\s*\d+(?:\.\d+)?(?![\d.])"),
    re.compile(r"(?<![\d.])\d+:\d+(?![\d.])"),
    re.compile(r"(?<![A-Za-z0-9])A[0-4](?![A-Za-z0-9])"),
]

_PLACEHOLDER = "SCGPH{:04d}"
_UNIT = re.compile(r"(?<![A-Za-z])(?:" + _UNIT_ALTS + r")(?![A-Za-z])|(?<=\d)\s*in\b", re.I)


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
    if "mm" in source_units and target_units.intersection({"in", "inch", "inches"}):
        issues.append("译文疑似把毫米换算成了英寸")
    if "kg" in source_units and target_units.intersection({"lb", "lbs"}):
        issues.append("译文疑似把千克换算成了磅")
    if "MPa" in source_units and "psi" in target_units:
        issues.append("译文疑似把兆帕换算成了 psi")
    source_standards = set(re.findall(r"(?<![A-Za-z0-9])(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN)(?![A-Za-z0-9])", source or "", flags=re.I))
    target_standards = set(re.findall(r"(?<![A-Za-z0-9])(?:GB|JB|ISO|DIN|ASTM|ANSI|JIS|EN)(?![A-Za-z0-9])", translated or "", flags=re.I))
    if source_standards and any(item.upper() not in {value.upper() for value in source_standards} for item in target_standards):
        issues.append("译文疑似替换了标准体系")
    return issues
