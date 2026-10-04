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


def _api_error(exc: Exception) -> RuntimeError:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return RuntimeError("Qwen API Key 无效，请检查 .env 中的 QWEN_API_KEY")
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
