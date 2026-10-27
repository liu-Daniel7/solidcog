# Translation MVP contract

This is the implementation contract for the first bilingual drawing view. It
is intentionally limited to a normalized, line-level table; exact recovery of
merged cells is a later phase.

## Scope

- Translate extracted OCR text only. Never re-upload or re-render the drawing.
- First target language: English (`en`).
- Source language: Chinese when the text contains CJK characters, otherwise
  `auto`.
- Provider: Qwen-MT (`qwen-mt-plus` by default), sharing `QWEN_API_KEY` with
  Qwen-VL.
- Sections: title block (`title`), technical requirements (`tech`), and unique
  short drawing labels/annotations (`extra`). Table data rows are not translated
  in this first version; that belongs to the later exact-table phase.

## Storage

```text
drawing_segments
  id, drawing_id, segment_key, section, ordinal, label,
  source_text, source_hash, translatable, meta_json, created_at
  UNIQUE(drawing_id, segment_key)

translations
  id, segment_id, target_lang, provider, model,
  translated_text, status, source_hash, glossary_hash,
  error, input_tokens, output_tokens, updated_at
  UNIQUE(segment_id, target_lang, provider)

glossary_terms
  id, source_lang, target_lang, source, target, enabled, note
  UNIQUE(source_lang, target_lang, source)
```

`status` values stored in `translations` are `translated`, `skipped`,
`needs_review`, and `failed`. A cached translation is reported by the API as
`cached` without changing the stored status.

## Endpoints

`GET /drawings/{drawing_id}/translations?target_lang=en`

`POST /drawings/{drawing_id}/translations`

```json
{
  "target_lang": "en",
  "force": false,
  "sections": ["title", "tech", "extra"]
}
```

`GET /drawings/{drawing_id}/translations.csv?target_lang=en`

The JSON response:

```json
{
  "drawing_id": 1,
  "filename": "drawing.pdf",
  "target_lang": "en",
  "provider": "qwen-mt",
  "model": "qwen-mt-plus",
  "segments": [
    {
      "id": 1,
      "segment_key": "title:001",
      "section": "title",
      "section_label": "标题栏",
      "label": "图号",
      "source_text": "LJT01.01",
      "translated_text": "LJT01.01",
      "status": "skipped",
      "error": null,
      "updated_at": "2026-10-27 10:00:00"
    }
  ],
  "summary": {
    "total": 1,
    "translated": 0,
    "cached": 0,
    "skipped": 1,
    "needs_review": 0,
    "failed": 0,
    "missing": 0
  },
  "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

`GET /translate/{drawing_id}` renders the page. The page loads the JSON
endpoint, shows the source and translation columns, and posts to the translate
endpoint. `GET` must not trigger paid model calls.

The CSV is UTF-8 with a BOM and has these columns:

`序号, 区域, 位置/字段, 原文, 译文, 状态`

## Frontend contract

- Use the existing SolidCog topbar and visual language.
- One target-language select, English first.
- Buttons: translate missing entries, force re-translate, export CSV.
- Show an explicit privacy note: only extracted text is sent; drawing images
  are not uploaded.
- Render source/translation text with `textContent`, not HTML interpolation.
- Show `missing`, `translated`, `cached`, `skipped`, `needs_review`, and
  `failed` states.
- The page must work at desktop and mobile widths.
