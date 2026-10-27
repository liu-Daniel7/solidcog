"""Conservative visual review for possibly unlabelled drawing geometry.

The detector finds enclosed white regions (holes, slots and closed outlines) in
the raster page and checks whether OCR evidence has dimension text near them.
It is deliberately a review aid: a detected feature is never reported as
"checked and complete". Open outlines, view relationships, OCR omissions,
low-contrast scans and semantic dimension-to-feature matching remain outside
what this deterministic MVP can prove.
"""

from __future__ import annotations

import math
import re
from collections import deque
from pathlib import Path

from PIL import Image

from app import config
from app.services import images


_DIMENSION_TEXT = re.compile(
    r"(?:"
    r"[⌀φΦØ]\s*[0-9]+(?:\.[0-9]+)?|"
    r"R\s*[0-9]+(?:\.[0-9]+)?|"
    r"M\s*[0-9]+(?:[x×][0-9]+)?(?:-[0-9A-Z]+)?|"
    r"[0-9]+(?:\.[0-9]+)?\s*(?:±|\+|-)\s*[0-9]+(?:\.[0-9]+)?|"
    r"[0-9]+(?:\.[0-9]+)?\s*(?:mm|°|′|″)?"
    r")",
    re.I,
)
_MAX_IMAGE_SIDE = 1100
_MIN_COMPONENT_AREA = 70
_MAX_CANDIDATES = 10


def _otsu_threshold(histogram: list[int], total: int) -> int:
    total_sum = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = -1.0
    best_threshold = 200
    for threshold, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += threshold * count
        background_mean = background_sum / background_weight
        foreground_mean = (total_sum - background_sum) / foreground_weight
        variance = (
            background_weight
            * foreground_weight
            * (background_mean - foreground_mean) ** 2
        )
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def _connected_enclosed_components(gray) -> tuple[list[dict], dict]:
    width, height = gray.size
    pixels = gray.tobytes()
    histogram = [0] * 256
    for value in pixels:
        histogram[value] += 1
    threshold = _otsu_threshold(histogram, width * height)
    ink_ratio = sum(histogram[: threshold + 1]) / max(1, width * height)
    # Otsu's threshold partitions ink (<= threshold) from paper (> threshold).
    white = bytearray(1 if value > threshold else 0 for value in pixels)
    visited = bytearray(width * height)
    queue: deque[int] = deque()

    def enqueue(index: int) -> None:
        if white[index] and not visited[index]:
            visited[index] = 1
            queue.append(index)

    for x in range(width):
        enqueue(x)
        enqueue((height - 1) * width + x)
    for y in range(height):
        enqueue(y * width)
        enqueue(y * width + width - 1)
    while queue:
        index = queue.popleft()
        x = index % width
        if x and white[index - 1] and not visited[index - 1]:
            visited[index - 1] = 1
            queue.append(index - 1)
        if x + 1 < width and white[index + 1] and not visited[index + 1]:
            visited[index + 1] = 1
            queue.append(index + 1)
        if index >= width and white[index - width] and not visited[index - width]:
            visited[index - width] = 1
            queue.append(index - width)
        if index + width < width * height and white[index + width] and not visited[index + width]:
            visited[index + width] = 1
            queue.append(index + width)

    components = []
    for start in range(width * height):
        if not white[start] or visited[start]:
            continue
        queue.append(start)
        visited[start] = 1
        area = 0
        left = width
        top = height
        right = 0
        bottom = 0
        while queue:
            index = queue.popleft()
            area += 1
            x = index % width
            y = index // width
            left = min(left, x)
            right = max(right, x)
            top = min(top, y)
            bottom = max(bottom, y)
            if x and white[index - 1] and not visited[index - 1]:
                visited[index - 1] = 1
                queue.append(index - 1)
            if x + 1 < width and white[index + 1] and not visited[index + 1]:
                visited[index + 1] = 1
                queue.append(index + 1)
            if index >= width and white[index - width] and not visited[index - width]:
                visited[index - width] = 1
                queue.append(index - width)
            if index + width < width * height and white[index + width] and not visited[index + width]:
                visited[index + width] = 1
                queue.append(index + width)
        if area:
            components.append(
                {
                    "area": area,
                    "left": left,
                    "top": top,
                    "right": right + 1,
                    "bottom": bottom + 1,
                }
            )
            if len(components) > 3000:
                break
    return components, {
        "threshold": threshold,
        "ink_ratio": ink_ratio,
        "width": width,
        "height": height,
    }


def _text_blocks(page_evidence: dict | None) -> list[dict]:
    blocks = []
    excluded_types = {
        "header", "footer", "aside_text", "page_number", "page_footer",
        "table", "table_body", "title_text",
    }
    for block in (page_evidence or {}).get("blocks") or []:
        bbox = block.get("bbox")
        block_type = str(block.get("type") or "").lower()
        text = str(block.get("text") or "").strip()
        if block_type in excluded_types or not text or not _DIMENSION_TEXT.search(text):
            continue
        # Border-zone letters/numbers and numbered technical notes are not
        # drawing dimensions. They must not count as nearby evidence.
        if re.fullmatch(r"(?:[0-9A-F]\s*[|\s]\s*){2,}[0-9A-F]", text):
            continue
        if re.match(r"^\s*[0-9]{1,3}[.、)]\s+", text) and (
            len(text) > 12 or "。" in text or "；" in text
        ):
            continue
        if re.search(r"技术要求|技术条件|未注|工艺要求|GB/T\s*[0-9]", text):
            continue
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            bbox = None
        else:
            try:
                bbox = [float(item) for item in bbox]
            except (TypeError, ValueError):
                bbox = None
            if bbox and bbox[0] > 0.52 and bbox[1] > 0.55:
                continue
        blocks.append({"text": text[:300], "bbox": bbox})
    return blocks


def _near_dimension(bbox: list[float], blocks: list[dict]) -> dict | None:
    x0, y0, x1, y1 = bbox
    best = None
    best_distance = 1e9
    # A dimension leader/suffix is usually close to its feature. This proximity
    # test is evidence for review, not proof of correctness.
    limit = max(0.045, (x1 - x0) * 1.8, (y1 - y0) * 1.8)
    for block in blocks:
        box = block.get("bbox")
        if not box:
            continue
        bx0, by0, bx1, by1 = box
        dx = max(0.0, bx0 - x1, x0 - bx1)
        dy = max(0.0, by0 - y1, y0 - by1)
        distance = math.hypot(dx, dy)
        if distance <= limit and distance < best_distance:
            best = block
            best_distance = distance
    return best


def _is_probable_text_hole(component: dict, width: int, height: int, blocks: list[dict]) -> bool:
    box_width = component["right"] - component["left"]
    box_height = component["bottom"] - component["top"]
    if box_width > 0.018 * width or box_height > 0.022 * height:
        return False
    cx = (component["left"] + component["right"]) / 2 / width
    cy = (component["top"] + component["bottom"]) / 2 / height
    for block in blocks:
        box = block.get("bbox")
        if not box:
            continue
        if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
            if (box[2] - box[0]) * (box[3] - box[1]) < 0.12:
                return True
    return False


def _find_candidates(image, page_evidence: dict | None) -> dict:
    width, height = image.size
    scale = min(1.0, _MAX_IMAGE_SIDE / max(width, height))
    work = image.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        resample=Image.Resampling.LANCZOS,
    ).convert("L")
    try:
        components, quality = _connected_enclosed_components(work)
    finally:
        work.close()

    if quality["ink_ratio"] < 0.00015 or quality["ink_ratio"] > 0.46:
        return {
            "candidates": [],
            "quality": "low",
            "reason": (
                "图像对比度过低或黑色像素比例异常，无法可靠分析孔槽与轮廓"
                if quality["ink_ratio"] < 0.00015 or quality["ink_ratio"] > 0.46
                else ""
            ),
            "image_size": [width, height],
        }

    work_width = quality["width"]
    work_height = quality["height"]
    text_blocks = _text_blocks(page_evidence)
    candidates = []
    for component in components:
        box_width = component["right"] - component["left"]
        box_height = component["bottom"] - component["top"]
        area_ratio = component["area"] / (work_width * work_height)
        bbox_area = max(1, box_width * box_height)
        fill_ratio = component["area"] / bbox_area
        if box_width < 9 or box_height < 8 or component["area"] < _MIN_COMPONENT_AREA:
            continue
        if area_ratio > 0.45:
            continue
        if box_width > 0.94 * work_width and box_height > 0.94 * work_height:
            continue
        if box_width > 0.65 * work_width and box_height > 0.65 * work_height:
            continue  # full sheet/frame interior, not a reviewable feature
        if (
            component["left"] < 0.025 * work_width
            or component["top"] < 0.025 * work_height
            or component["right"] > 0.975 * work_width
            or component["bottom"] > 0.975 * work_height
        ):
            continue  # sheet frame / zone boxes
        cx = (component["left"] + component["right"]) / 2
        cy = (component["top"] + component["bottom"]) / 2
        if cx > 0.52 * work_width and cy > 0.55 * work_height:
            continue  # title block / revision table
        if _is_probable_text_hole(component, work_width, work_height, text_blocks):
            continue
        aspect = box_width / max(1, box_height)
        if 0.72 <= aspect <= 1.4 and 0.55 <= fill_ratio <= 0.88:
            feature = "疑似孔/圆形特征"
        elif 1.25 <= aspect <= 5.5 and 0.32 <= fill_ratio <= 0.94:
            feature = "疑似槽/长圆特征"
        elif aspect < 1.25 and fill_ratio > 0.93:
            feature = "疑似矩形封闭轮廓"
        else:
            feature = "疑似封闭轮廓"
        bbox = [
            round(component["left"] / work_width, 6),
            round(component["top"] / work_height, 6),
            round(component["right"] / work_width, 6),
            round(component["bottom"] / work_height, 6),
        ]
        candidates.append(
            {
                "type": feature,
                "bbox": bbox,
                "area_ratio": area_ratio,
                "fill_ratio": fill_ratio,
                "aspect": aspect,
                "confidence": round(
                    min(0.82, 0.46 + min(0.24, area_ratio * 12) + min(0.12, fill_ratio / 8)),
                    3,
                ),
            }
        )
    candidates.sort(key=lambda item: (item["area_ratio"], item["fill_ratio"]), reverse=True)
    return {
        "candidates": candidates[:_MAX_CANDIDATES],
        "quality": "ok",
        "reason": "",
        "image_size": [width, height],
    }


def inspect_drawing(
    path: Path,
    pages_evidence: list[dict],
    *,
    max_pages: int | None = None,
) -> dict:
    """Inspect raster pages and return review-only dimension findings."""
    page_limit = max(1, max_pages or config.COMPLETENESS_DIMENSION_MAX_PAGES)
    try:
        count = images.page_count(path)
    except Exception as exc:
        return {
            "failed": True,
            "error": f"无法读取原图页数：{exc}",
            "pages_scanned": 0,
            "candidates": [],
            "limitations": [],
        }
    evidence_by_page = {
        int(page.get("page_number") or 1): page for page in pages_evidence or []
    }
    candidates = []
    limitations = []
    scanned = 0
    if count > page_limit:
        limitations.append(
            f"图纸共 {count} 页，自动几何审查仅分析前 {page_limit} 页；其余页面需人工复核"
        )
    for page_number in range(1, min(count, page_limit) + 1):
        try:
            image = images.load_page(path, page_number, dpi=140)
        except Exception as exc:
            limitations.append(f"第 {page_number} 页无法渲染：{exc}")
            continue
        try:
            result = _find_candidates(image, evidence_by_page.get(page_number))
        finally:
            image.close()
        scanned += 1
        if result["quality"] != "ok":
            limitations.append(f"第 {page_number} 页：{result['reason']}")
            continue
        blocks = _text_blocks(evidence_by_page.get(page_number))
        coordinate_blocks = [block for block in blocks if block.get("bbox")]
        for item in result["candidates"]:
            nearby = _near_dimension(item["bbox"], coordinate_blocks)
            if not coordinate_blocks:
                detail = (
                    "检测到封闭几何特征，但当前 OCR 没有可用坐标，无法判断附近是否已有尺寸文字；需人工复核"
                )
                confidence = min(float(item["confidence"]), 0.6)
            elif nearby:
                detail = (
                    f"检测到{str(item['type']).removeprefix('疑似')}，附近存在尺寸文字“{nearby['text'][:80]}”，"
                    "但无法证明该尺寸对应此特征；需人工确认"
                )
                confidence = min(float(item["confidence"]), 0.58)
            else:
                detail = (
                    f"检测到{str(item['type']).removeprefix('疑似')}，附近未识别到尺寸文字，"
                    "疑似漏标；OCR 可能漏字，需人工复核"
                )
                confidence = item["confidence"]
            candidates.append(
                {
                    "page_number": page_number,
                    "bbox": item["bbox"],
                    "type": item["type"],
                    "area_ratio": item["area_ratio"],
                    "fill_ratio": item["fill_ratio"],
                    "aspect": item["aspect"],
                    "confidence": confidence,
                    "detail": detail,
                    "near_dimension": nearby["text"][:120] if nearby else "",
                    "coordinate_association": bool(coordinate_blocks),
                }
            )
    return {
        "failed": scanned == 0,
        "error": "；".join(limitations) if scanned == 0 else "",
        "pages_scanned": scanned,
        "page_count": count,
        "candidates": candidates,
        "limitations": limitations,
    }
