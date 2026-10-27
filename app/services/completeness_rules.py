"""Versioned rule catalog for completeness checks.

Only existence/emptiness is checked. These rules intentionally do not encode
engineering tolerances, material validity or design correctness.
"""

from __future__ import annotations

CATALOG_VERSION = "solidcog-completeness-2026-10-27"

TITLE_FIELD_CATALOG = (
    {
        "key": "drawing_no",
        "label": "图号",
        "aliases": [
            "图号", "图纸编号", "图纸号", "图样代号", "图样编号", "零件号",
            "零件代号", "零件编号", "代号", "DRAWING NO", "DRAWING NUMBER",
            "DWG NO", "DWG NUMBER", "PART NO", "PART NUMBER",
        ],
    },
    {
        "key": "drawing_name",
        "label": "图名",
        "aliases": [
            "图名", "图纸名称", "图样名称", "零件名称", "名称", "DRAWING NAME",
            "PART NAME",
        ],
    },
    {"key": "material", "label": "材料", "aliases": ["材料", "材质", "MATERIAL"]},
    {"key": "scale", "label": "比例", "aliases": ["比例", "SCALE"]},
    {
        "key": "revision",
        "label": "版次",
        "aliases": ["版次", "版本", "版号", "更改版次", "REV", "REVISION"],
    },
    {"key": "date", "label": "日期", "aliases": ["日期", "年月日", "DATE"]},
    {"key": "weight", "label": "重量", "aliases": ["重量", "质量", "WEIGHT", "MASS"]},
    {"key": "designer", "label": "设计/制图", "aliases": ["设计", "制图", "DRAWN", "DESIGNED"]},
    {"key": "checker", "label": "校对/审核", "aliases": ["校对", "审核", "CHECKED", "APPROVED"]},
)

BOM_COLUMN_CATALOG = (
    {"key": "sequence", "label": "序号", "aliases": ["序号", "项号", "ITEM", "ITEM NO"]},
    {
        "key": "drawing_no",
        "label": "图号/代号",
        "aliases": [
            "图号",
            "代号",
            "图纸编号",
            "图纸号",
            "图样代号",
            "图样编号",
            "零件号",
            "零件代号",
            "零件编号",
            "DRAWING NO",
            "DRAWING NUMBER",
            "DWG NO",
            "DWG NUMBER",
            "PART NO",
            "PART NUMBER",
            "CODE",
        ],
    },
    {
        "key": "name",
        "label": "名称",
        "aliases": ["名称", "零件名称", "图名", "图纸名称", "NAME", "PART NAME"],
    },
    {
        "key": "quantity",
        "label": "数量",
        "aliases": ["数量", "件数", "数量/件", "QTY", "QUANTITY"],
    },
    {"key": "material", "label": "材料", "aliases": ["材料", "材质", "MATERIAL", "MAT'L"]},
    {"key": "remark", "label": "备注", "aliases": ["备注", "说明", "REMARK", "NOTE"]},
)

DEFAULT_REQUIRED_TITLE_FIELDS = (
    "drawing_no",
    "drawing_name",
    "material",
    "scale",
    "revision",
    "date",
)
DEFAULT_BOM_REQUIRED_COLUMNS = ("sequence", "drawing_no", "name", "quantity")


def _catalog_by_key(catalog) -> dict[str, dict]:
    return {item["key"]: item for item in catalog}


TITLE_FIELDS = _catalog_by_key(TITLE_FIELD_CATALOG)
BOM_COLUMNS = _catalog_by_key(BOM_COLUMN_CATALOG)


def default_rules() -> dict:
    return normalize_rules(
        {
            "required_title_fields": list(DEFAULT_REQUIRED_TITLE_FIELDS),
            "bom_required_columns": list(DEFAULT_BOM_REQUIRED_COLUMNS),
            "dimension_check": True,
        }
    )


def normalize_rules(payload: dict | None) -> dict:
    """Validate a rule payload and return a self-describing snapshot."""
    source = payload or {}
    title_keys = source.get("required_title_fields", list(DEFAULT_REQUIRED_TITLE_FIELDS))
    bom_keys = source.get("bom_required_columns", list(DEFAULT_BOM_REQUIRED_COLUMNS))
    if not isinstance(title_keys, list) or not isinstance(bom_keys, list):
        raise ValueError("必填字段配置格式无效")
    unknown_title = [key for key in title_keys if key not in TITLE_FIELDS]
    unknown_bom = [key for key in bom_keys if key not in BOM_COLUMNS]
    if unknown_title or unknown_bom:
        raise ValueError("必填字段配置包含未知字段")
    # Deduplicate while preserving catalog order for stable results.
    title_set = {str(key) for key in title_keys}
    bom_set = {str(key) for key in bom_keys}
    if not title_set and not bom_set and not source.get("dimension_check", True):
        raise ValueError("至少启用一项检查规则")
    return {
        "catalog_version": CATALOG_VERSION,
        "title_fields": [
            {
                "key": item["key"],
                "label": item["label"],
                "required": item["key"] in title_set,
            }
            for item in TITLE_FIELD_CATALOG
        ],
        "required_title_fields": [
            item["key"] for item in TITLE_FIELD_CATALOG if item["key"] in title_set
        ],
        "bom_columns": [
            {
                "key": item["key"],
                "label": item["label"],
                "required": item["key"] in bom_set,
            }
            for item in BOM_COLUMN_CATALOG
        ],
        "bom_required_columns": [
            item["key"] for item in BOM_COLUMN_CATALOG if item["key"] in bom_set
        ],
        "dimension_check": bool(source.get("dimension_check", True)),
    }


def required_title_keys(rules: dict) -> list[str]:
    return list(rules.get("required_title_fields") or DEFAULT_REQUIRED_TITLE_FIELDS)


def required_bom_keys(rules: dict) -> list[str]:
    return list(rules.get("bom_required_columns") or DEFAULT_BOM_REQUIRED_COLUMNS)
