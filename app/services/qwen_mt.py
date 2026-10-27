"""Qwen-MT translation channel.

The API key is intentionally shared with the Qwen-VL OCR channel by default.
Both model families can be reached from the same Model Studio account when the
configured base URL and model access match the key's region.
"""

from app import config


def _client():
    from openai import OpenAI

    return OpenAI(
        api_key=config.require_config("QWEN_API_KEY", config.QWEN_API_KEY),
        base_url=config.QWEN_MT_BASE_URL,
    )


def _api_error(exc: Exception) -> RuntimeError:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return RuntimeError("Qwen-MT API Key 无效，请检查 Key 与接口区域是否匹配")
    if status == 403:
        return RuntimeError("Qwen-MT 未开通或没有可用额度，请检查百炼控制台")
    if status == 404:
        return RuntimeError("当前接口未提供 Qwen-MT，请检查 QWEN_MT_BASE_URL 和模型名称")
    if status == 429:
        return RuntimeError("Qwen-MT 请求过于频繁，请稍后重试")
    return RuntimeError(f"Qwen-MT API 调用失败: {exc}")


LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
    "it": "Italian",
}


def translate_text(
    text: str,
    target_lang: str = "en",
    source_lang: str = "zh",
    terms: list[dict] | None = None,
    domains: str | None = None,
) -> dict:
    """Translate one segment and return text plus token usage."""
    options = {
        "source_lang": LANGUAGE_NAMES.get(source_lang, "auto" if source_lang == "auto" else source_lang),
        "target_lang": LANGUAGE_NAMES.get(target_lang, target_lang),
    }
    if terms:
        options["terms"] = terms
    if domains:
        options["domains"] = domains
    completion = _client().chat.completions.create(
        model=config.QWEN_MT_MODEL,
        messages=[{"role": "user", "content": text}],
        extra_body={"translation_options": options},
        timeout=config.QWEN_MT_TIMEOUT_SECONDS,
    )
    choice = completion.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError("Qwen-MT 输出达到长度限制，译文可能不完整")
    translated = (choice.message.content or "").strip()
    if not translated:
        raise RuntimeError("Qwen-MT 返回空结果")
    usage = getattr(completion, "usage", None)
    return {
        "text": translated,
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def test_connection() -> dict:
    """Make a tiny translation request to verify the shared key and model access."""
    try:
        result = translate_text("测试", target_lang="en", source_lang="zh")
        return {
            "ok": True,
            "model": config.QWEN_MT_MODEL,
            "message": "连接正常",
            "sample": result["text"][:80],
        }
    except Exception as exc:
        return {
            "ok": False,
            "model": config.QWEN_MT_MODEL,
            "message": str(_api_error(exc)),
        }
