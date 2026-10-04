"""High-resolution views used to read dense mechanical drawings."""

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class OcrView:
    name: str
    image: Image.Image
    purpose: str


def _crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = box
    return image.crop((left, top, right, bottom)).convert("RGB")


def _region_box(width: int, height: int, x0: float, y0: float, x1: float, y1: float):
    return (int(width * x0), int(height * y0), int(width * x1), int(height * y1))


def _bounded_zone(width: int, height: int, zone: tuple[int, int, int, int], anchor_x: str, anchor_y: str, limit: int):
    left, top, right, bottom = zone
    crop_width = min(right - left, limit)
    crop_height = min(bottom - top, limit)
    if anchor_x == "right":
        left = right - crop_width
    if anchor_y == "bottom":
        top = bottom - crop_height
    return (max(0, left), max(0, top), min(width, left + crop_width), min(height, top + crop_height))


def iter_overlapping_tiles(image: Image.Image, tile_size: int = 2200, overlap: float = 0.16):
    """Yield (left, top, right, bottom), crop pairs covering the entire image."""
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("图纸图像尺寸无效")
    if tile_size <= 0 or not 0 <= overlap < 1:
        raise ValueError("tile_size 必须为正数，overlap 必须在 [0, 1) 内")
    tile_width = min(width, tile_size)
    tile_height = min(height, tile_size)
    step_x = max(1, int(tile_width * (1 - overlap)))
    step_y = max(1, int(tile_height * (1 - overlap)))
    xs = list(range(0, max(1, width - tile_width + 1), step_x))
    ys = list(range(0, max(1, height - tile_height + 1), step_y))
    if not xs or xs[-1] + tile_width < width:
        xs.append(max(0, width - tile_width))
    if not ys or ys[-1] + tile_height < height:
        ys.append(max(0, height - tile_height))
    for top in ys:
        for left in xs:
            box = (left, top, left + tile_width, top + tile_height)
            yield box, image.crop(box).convert("RGB")


def build_ocr_views(image: Image.Image, tile_size: int = 2200, overlap: float = 0.16) -> list[OcrView]:
    """Return an overview, complete overlapping tile coverage, and field crops."""
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("图纸图像尺寸无效")
    views = [OcrView("overview", image, "判断图纸方向、版面及内容分布")]

    for box, tile in iter_overlapping_tiles(image, tile_size, overlap):
        left, top, right, bottom = box
        if right - left == width and bottom - top == height:
            continue
        views.append(OcrView(f"tile_{left}_{top}", tile, "逐字读取此局部中的全部文字、尺寸、公差和符号"))

    # Standards vary; probe broad sheet-edge zones where these blocks usually live.
    title_box = _bounded_zone(width, height, _region_box(width, height, 0.52, 0.55, 1.0, 1.0), "right", "bottom", tile_size)
    tech_boxes = [
        _region_box(width, height, 0.0, 0.0, 0.52, 0.48),
        _region_box(width, height, 0.0, 0.52, 0.52, 1.0),
        _region_box(width, height, 0.48, 0.0, 1.0, 0.48),
    ]
    tech_boxes = [_bounded_zone(width, height, box, "left", "top", tile_size) for box in tech_boxes]
    views.append(OcrView("title_candidate", _crop(image, title_box), "专门提取标题栏字段：名称、图号、材料、比例、数量等"))
    for index, box in enumerate(tech_boxes, 1):
        views.append(OcrView(f"tech_candidate_{index}", _crop(image, box), "查找技术要求、工艺说明及其完整编号条目"))
    return views


def merge_lines(texts: list[str]) -> str:
    """Merge fields while removing only literally identical duplicate lines."""
    lines = []
    seen = set()
    for text in texts:
        for line in str(text).splitlines():
            cleaned = line.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                lines.append(cleaned)
    return "\n".join(lines)
