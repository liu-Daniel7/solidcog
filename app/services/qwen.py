import base64
import io
import json
import re

from PIL import Image

from app import config

OCR_PROMPT = """你是机械工程图纸 OCR 助手。请忠实读取本页图纸，不推测看不清的内容。
返回一个 JSON 对象，且只返回 JSON：
{
  "title_block": "标题栏文字，包括图号、名称、比例、材料等",
  "tech_block": "技术要求与工艺要求文字",
  "all_text": "本页所有可辨识文字、尺寸、公差和符号",
  "layout": "horizontal 或 vertical"
}
看不清的字段使用空字符串。保留原始数值、单位、正负号、直径和公差符号。"""


def _client():
    from openai import OpenAI

    return OpenAI(
        api_key=config.require_config("QWEN_API_KEY", config.QWEN_API_KEY),
        base_url=config.QWEN_BASE_URL,
    )


def _image_url(image: Image.Image) -> str:
    image = image.convert("RGB")
    image.thumbnail((4096, 4096))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {"title_block": "", "tech_block": "", "all_text": cleaned, "layout": "unknown"}
        data = json.loads(match.group(0))
    return {
        "title_block": str(data.get("title_block", "")),
        "tech_block": str(data.get("tech_block", "")),
        "all_text": str(data.get("all_text", "")),
        "layout": str(data.get("layout", "unknown")),
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
        completion = _client().chat.completions.create(
            model=config.QWEN_VL_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": f"这是第 {page_number} 页。\n{OCR_PROMPT}"},
                    {"type": "image_url", "image_url": {"url": _image_url(image)}},
                ],
            }],
            stream=False,
        )
    except Exception as exc:
        raise _api_error(exc) from exc
    result = _parse_json(completion.choices[0].message.content or "")
    result["page"] = page_number
    return result
