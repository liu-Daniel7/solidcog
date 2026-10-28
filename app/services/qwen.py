import base64
import io
import json
import re

from PIL import Image

from app import config
from app.services.drawing_ocr_images import build_ocr_views, merge_lines

OCR_PROMPT = """你是机械工程图纸 OCR 助手。请忠实读取本页图纸，不推测看不清的内容。
返回一个 JSON 对象，且只返回 JSON：
{
  "title_block": "标题栏文字，包括图号、名称、比例、材料等",
  "tech_block": "技术要求与工艺要求文字",
  "all_text": "本页所有可辨识文字、尺寸、公差和符号",
  "layout": "horizontal 或 vertical"
}
看不清的字段使用空字符串。保留原始数值、单位、正负号、直径和公差符号。不要根据常识补全字符；区分相似字符（0/O、1/I、5/S），保留原有换行和编号。"""


def _client():
    from openai import OpenAI

    return OpenAI(
        api_key=config.require_config("QWEN_API_KEY", config.QWEN_API_KEY),
        base_url=config.QWEN_BASE_URL,
    )


def _image_url(image: Image.Image) -> str:
    encoded_image = image.convert("RGB")
    try:
        # Only the overview is downsampled. Detail crops retain their source pixels.
        encoded_image.thumbnail((4096, 4096))
        buffer = io.BytesIO()
        encoded_image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    finally:
        encoded_image.close()
    return f"data:image/png;base64,{encoded}"


def _connection_image_url() -> str:
    """Small valid image used only by the settings connection check."""
    image = Image.new("RGB", (64, 64), "white")
    try:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    finally:
        image.close()
    return f"data:image/png;base64,{encoded}"


def test_connection() -> dict:
    """Verify that the saved key can reach the configured Qwen-VL model."""
    try:
        completion = _client().chat.completions.create(
            model=config.QWEN_VL_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "这是一张连接测试图片。只回复 OK。"},
                    {"type": "image_url", "image_url": {"url": _connection_image_url()}},
                ],
            }],
            max_tokens=16,
            timeout=20,
        )
        answer = (completion.choices[0].message.content or "").strip()
        if not answer:
            raise RuntimeError("Qwen-VL 返回空结果")
        return {
            "ok": True,
            "model": config.QWEN_VL_MODEL,
            "message": "连接正常",
            "sample": answer[:80],
        }
    except Exception as exc:
        return {
            "ok": False,
            "model": config.QWEN_VL_MODEL,
            "message": str(_api_error(exc)),
        }


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {"title_block": "", "tech_block": "", "all_text": cleaned, "layout": "unknown"}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"title_block": "", "tech_block": "", "all_text": cleaned, "layout": "unknown"}
    if not isinstance(data, dict):
        return {"title_block": "", "tech_block": "", "all_text": "", "layout": "unknown"}
    def field_text(value):
        if value is None or isinstance(value, (dict, list, tuple)):
            return ""
        return str(value)

    return {
        "title_block": field_text(data.get("title_block", "")),
        "tech_block": field_text(data.get("tech_block", "")),
        "all_text": field_text(data.get("all_text", "")),
        "layout": field_text(data.get("layout", "unknown")) or "unknown",
    }


def _parse_raw_json_object(text: str) -> dict:
    """Parse a model's JSON object without reducing it to OCR fields."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("模型返回的 JSON 不是对象")
    return data


def _api_error(exc: Exception) -> RuntimeError:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return RuntimeError("Qwen API Key 无效，请在右上角“处理设置”中重新填写")
    if status == 403:
        return RuntimeError("Qwen API 无可用额度，请充值或关闭阿里云控制台的“仅使用免费额度”模式")
    if status == 429:
        return RuntimeError("Qwen API 请求过于频繁，请稍后重试")
    return RuntimeError(f"Qwen3-VL API 调用失败: {exc}")


def ocr_page(image: Image.Image, page_number: int) -> dict:
    try:
        client = _client()
    except Exception as exc:
        raise _api_error(exc) from exc
    results = []
    failures = []
    views = build_ocr_views(image)
    try:
        for view in views:
            task_prompt = OCR_PROMPT
            if view.name == "overview":
                task_prompt += "\n本图为整页概览，重点判断横向或纵向布局，并识别可读内容。"
            elif view.name.startswith("tile_"):
                task_prompt += "\n本图为高分辨率局部，准确抄录其中所有可见文字和工程标注。"
            elif view.name.startswith("title_"):
                task_prompt += "\n重点检查标题栏，只填写本区域实际出现的标题栏内容，不确定字段留空。"
            else:
                task_prompt += "\n重点查找技术要求/工艺文字，只填写本区域实际出现的内容。"
            try:
                completion = client.chat.completions.create(
                    model=config.QWEN_VL_MODEL,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"这是第 {page_number} 页，视图用途：{view.purpose}。\n{task_prompt}"},
                            {"type": "image_url", "image_url": {"url": _image_url(view.image)}},
                        ],
                    }],
                    stream=False,
                )
                choice = completion.choices[0]
                results.append((view.name, _parse_json(choice.message.content or "")))
                if getattr(choice, "finish_reason", None) == "length":
                    failures.append((view.name, RuntimeError("模型输出达到长度限制，本区域文字可能不完整")))
            except Exception as exc:
                failures.append((view.name, exc))
            finally:
                if view.image is not image:
                    view.image.close()
    finally:
        for view in views:
            if view.image is not image:
                try:
                    view.image.close()
                except Exception:
                    pass
    if not results:
        if failures:
            view_name, error = failures[0]
            raise _api_error(error) from error
        raise RuntimeError("Qwen3-VL 没有可用识别视图")
    result_by_name = dict(results)
    overview = result_by_name.get("overview")
    title_text = merge_lines([result["title_block"] for _, result in results])
    tech_text = merge_lines([result["tech_block"] for _, result in results])
    text_blocks = []
    for name, result in results:
        if result["all_text"].strip():
            label = "整页" if name == "overview" else f"局部 {name.removeprefix('tile_')}" if name.startswith("tile_") else name
            text_blocks.append(f"[{label}]\n{result['all_text'].strip()}")
    all_text = "\n\n".join(text_blocks)
    if not (title_text or tech_text or all_text):
        raise RuntimeError("Qwen3-VL 未识别出可辨识文字")
    result = {
        "title_block": title_text,
        "tech_block": tech_text,
        "all_text": all_text,
        "layout": (overview["layout"] if overview and overview["layout"] in {"horizontal", "vertical"}
                   else ("horizontal" if image.width >= image.height else "vertical")),
    }
    if failures:
        result["warnings"] = [f"{view_name}: {_api_error(exc)}" for view_name, exc in failures]
    result["page"] = page_number
    return result


BOM_PROMPT = """你是机械装配图明细表（BOM/明细栏）结构化提取助手。只读取本页图纸中的装配明细表，不要提取标题栏、技术要求、尺寸表或零件图的参数表。
返回一个 JSON 对象，且只返回 JSON：
{
  "has_table": true 或 false,
  "table_kind": "assembly_bom" | "part_table" | "title_block" | "other" | "none",
  "title_block_code": "本页标题栏中的图纸代号，没有则空字符串",
  "title_block_name": "本页标题栏中的图纸名称，没有则空字符串",
  "rows": [
    {
      "seq": "明细表序号",
      "code": "代号/图号",
      "name": "名称",
      "note": "备注原文",
      "qty": "数量",
      "raw_text": "该行原始文字"
    }
  ],
  "issues": ["无法确定的问题，没有则空数组"]
}
规则：
- 只输出装配明细表的行；表头重复出现时跳过表头，不要把表头当成行。
- 明细表可能是正序或倒序；按字段含义对齐，不要假设固定行序或固定列号。
- 当前图纸自身的图号只属于标题栏，绝不能作为明细表子项。
- 保持同一行的代号、名称、备注对应关系；备注按原文保留。
- 看不清的字段写空字符串，不要猜测、不要补全。不要根据文件名或常识编造内容。
- 如果页面旋转或倒置，请按文字可读方向读取。
- 先判断页面中是否存在明细栏/明细表的表格框线（通常在标题栏上方或附近）。只要看到表格框线，即使文字无法辨认，也要 has_table=true；此时 rows 可为空，并在 issues 中说明“表格存在但文字无法辨认”，不能把无法辨认误报为“没有明细表”。
- 如果页面确实没有装配明细表（例如只有标题栏或零件图参数表），has_table 为 false、rows 为空数组。
"""


def extract_bom_page(image: Image.Image, page_number: int = 1, filename_hint: str = "", title_hint: str = "") -> dict:
    """Read one drawing page and return a structured candidate BOM.

    This is a focused second OCR pass over an existing page image.  It never
    makes the exists/missing decision; that is done by deterministic rules.
    """
    hint = ""
    if filename_hint:
        hint += f"\n文件名提示（只用于定位图纸，不要据此编造表格内容）：{filename_hint}"
    if title_hint:
        hint += f"\n标题栏提示（仅作参考，读不到就留空）：{title_hint}"
    completion = _client().chat.completions.create(
        model=config.QWEN_VL_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": f"这是第 {page_number} 页。{hint}\n{BOM_PROMPT}"},
                {"type": "image_url", "image_url": {"url": _image_url(image)}},
            ],
        }],
        max_tokens=4000,
        timeout=120,
    )
    choice = completion.choices[0]
    content = (choice.message.content or "").strip()
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError("Qwen3-VL 明细表识别输出达到长度限制，结果可能不完整")
    if not content:
        raise RuntimeError("Qwen3-VL 明细表识别返回空结果")
    try:
        data = _parse_raw_json_object(content)
    except Exception as exc:
        raise RuntimeError("Qwen3-VL 明细表识别返回内容不是有效 JSON: " + content[:240].replace("\n", " ")) from exc
    if not any(key in data for key in ("has_table", "rows", "table_kind")):
        raise RuntimeError("Qwen3-VL 明细表识别没有返回约定的 JSON 字段: " + content[:240].replace("\n", " "))
    return data
