from dataclasses import dataclass
from typing import Tuple, Dict
import numpy as np
import logging

logger = logging.getLogger("layout_splitter")

if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

logger.setLevel(logging.INFO)


@dataclass
class LayoutConfig:

    title_y_ratio: Tuple[float, float]
    title_x_ratio: Tuple[float, float]

    tech_y_ratio: Tuple[float, float]
    tech_x_ratio: Tuple[float, float]


HORIZONTAL_CONFIG = LayoutConfig(

    title_y_ratio=(0.78, 1.0),
    title_x_ratio=(0.55, 1.0),

    tech_y_ratio=(0.55, 0.78),
    tech_x_ratio=(0.55, 1.0)

)

VERTICAL_CONFIG = LayoutConfig(

    title_y_ratio=(0.85, 1.0),
    title_x_ratio=(0.0, 1.0),

    tech_y_ratio=(0.65, 0.85),
    tech_x_ratio=(0.0, 1.0)

)


def safe_crop(
    img: np.ndarray,
    y1: int,
    y2: int,
    x1: int,
    x2: int
) -> np.ndarray:

    height, width = img.shape[:2]

    y1 = max(0, min(height, y1))
    y2 = max(0, min(height, y2))

    x1 = max(0, min(width, x1))
    x2 = max(0, min(width, x2))

    if y2 <= y1 or x2 <= x1:
        raise ValueError(
            f"Invalid crop region: "
            f"({y1}, {y2}, {x1}, {x2})"
        )

    return img[y1:y2, x1:x2]


def detect_layout(img: np.ndarray) -> str:

    if img is None:
        raise ValueError("Image is None")

    if not isinstance(img, np.ndarray):
        raise TypeError("Image must be numpy.ndarray")

    height, width = img.shape[:2]

    if height == 0 or width == 0:
        raise ValueError("Invalid image size")

    layout = "horizontal" if width > height else "vertical"

    logger.info(
        f"Detected layout: {layout} "
        f"({width}x{height})"
    )

    return layout


def ratio_to_pixels(
    height: int,
    width: int,
    config: LayoutConfig
) -> Dict[str, Tuple[int, int, int, int]]:

    def convert(
        y_ratio,
        x_ratio
    ):

        y1 = int(height * y_ratio[0])
        y2 = int(height * y_ratio[1])

        x1 = int(width * x_ratio[0])
        x2 = int(width * x_ratio[1])

        return y1, y2, x1, x2

    return {

        "title":

        convert(
            config.title_y_ratio,
            config.title_x_ratio
        ),

        "tech":

        convert(
            config.tech_y_ratio,
            config.tech_x_ratio
        )

    }


def split_regions(
    img: np.ndarray
) -> Dict[str, np.ndarray]:

    layout = detect_layout(img)

    height, width = img.shape[:2]

    config = (
        HORIZONTAL_CONFIG
        if layout == "horizontal"
        else VERTICAL_CONFIG
    )

    regions = ratio_to_pixels(
        height,
        width,
        config
    )

    logger.info(
        f"Cropping regions using {layout} template"
    )

    title_y1, title_y2, title_x1, title_x2 = regions["title"]

    tech_y1, tech_y2, tech_x1, tech_x2 = regions["tech"]

    title_block = safe_crop(
        img,
        title_y1,
        title_y2,
        title_x1,
        title_x2
    )

    tech_block = safe_crop(
        img,
        tech_y1,
        tech_y2,
        tech_x1,
        tech_x2
    )

    return {

        "layout": layout,

        "title_block": title_block,

        "tech_requirement": tech_block
    }
