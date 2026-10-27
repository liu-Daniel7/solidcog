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


def test_connection() -> dict:
    """Make a tiny translation request to verify the shared key and model access."""
    try:
        completion = _client().chat.completions.create(
            model=config.QWEN_MT_MODEL,
            messages=[{"role": "user", "content": "测试"}],
            extra_body={
                "translation_options": {
                    "source_lang": "Chinese",
                    "target_lang": "English",
                }
            },
            timeout=min(20, config.QWEN_MT_TIMEOUT_SECONDS),
        )
        translated = (completion.choices[0].message.content or "").strip()
        if not translated:
            raise RuntimeError("Qwen-MT 返回空结果")
        return {
            "ok": True,
            "model": config.QWEN_MT_MODEL,
            "message": "连接正常",
            "sample": translated[:80],
        }
    except Exception as exc:
        return {
            "ok": False,
            "model": config.QWEN_MT_MODEL,
            "message": str(_api_error(exc)),
        }
