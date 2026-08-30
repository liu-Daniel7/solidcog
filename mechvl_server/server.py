import base64
import io
import os
import threading

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel
from transformers import AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig

MODEL_ID = os.getenv("MECHVL_MODEL_ID", "XiaofengAlg/MechVL-4B-RL")
MAX_NEW_TOKENS = int(os.getenv("MECHVL_MAX_NEW_TOKENS", "256"))

app = FastAPI(title="MechVL local inference service")
inference_lock = threading.Lock()
processor = None
model = None


class AnalyzeRequest(BaseModel):
    question: str
    ocr_context: str = ""
    image_base64: str


def load_model() -> None:
    global processor, model
    if model is not None:
        return
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID,
        device_map="auto",
        torch_dtype=torch.float16,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        ),
    )
    model.eval()


@app.on_event("startup")
def startup() -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in WSL2")
    load_model()


@app.get("/health")
def health():
    return {
        "status": "ready" if model is not None else "loading",
        "model": MODEL_ID,
        "cuda": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "busy": inference_lock.locked(),
    }


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    if not inference_lock.acquire(blocking=False):
        raise HTTPException(409, "model is busy")
    try:
        try:
            image = Image.open(io.BytesIO(base64.b64decode(request.image_base64, validate=True))).convert("RGB")
        except Exception as exc:
            raise HTTPException(400, "invalid image_base64") from exc

        prompt = f"""你是机械工程图纸分析助手。严格根据图纸图像和 OCR 内容回答问题；依据不足时明确说明，不得编造尺寸、材料、公差或标准条款。

OCR 内容：
{request.ocr_context}

用户问题：
{request.question}

请用简洁、专业、可执行的中文回答。"""
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ]}]
        inputs = processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        trimmed = [output[len(source):] for source, output in zip(inputs.input_ids, generated)]
        answer = processor.batch_decode(trimmed, skip_special_tokens=True)[0].strip()
        return {"answer": answer, "model": MODEL_ID}
    except torch.OutOfMemoryError as exc:
        torch.cuda.empty_cache()
        raise HTTPException(507, "CUDA out of memory") from exc
    finally:
        inference_lock.release()
