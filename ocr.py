import sys
from pathlib import Path

from dotenv import load_dotenv

import torch

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "ocr-input"
OUTPUT_DIR = BASE_DIR / "ocr-output"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MODEL_PATH = "zai-org/GLM-OCR"
PROMPT_TEXT = "Text Recognition:"
MAX_NEW_TOKENS = 8192

load_dotenv(BASE_DIR / ".env")

sys.stdout.reconfigure(encoding="utf-8")

from transformers import AutoProcessor, AutoModelForImageTextToText


def select_device():
    return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


def iter_images(input_dir):
    return sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def build_messages(image_path):
    return [
        {
            "role": "user",
            "content": [
                {"type": "image", "url": image_path.as_posix()},
                {"type": "text", "text": PROMPT_TEXT},
            ],
        }
    ]


def recognize_image(image_path, processor, model, device, max_new_tokens):
    inputs = processor.apply_chat_template(
        build_messages(image_path),
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    ).to(device)

    inputs.pop("token_type_ids", None)
    with torch.inference_mode():
        generated_ids = model.generate(**inputs, max_new_tokens=max_new_tokens)
    return processor.decode(
        generated_ids[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=False,
    )


def main():
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    image_paths = iter_images(INPUT_DIR)
    if not image_paths:
        print(f"No images found in: {INPUT_DIR}")
        print(f"Supported extensions: {', '.join(sorted(IMAGE_EXTENSIONS))}")
        return 0

    device = select_device()
    print(f"CUDA available: {torch.cuda.is_available()}")
    if device.type == "cuda":
        print(f"Using GPU: {torch.cuda.get_device_name(device)}")
    else:
        print("Using CPU")

    print("Loading model...")
    processor = AutoProcessor.from_pretrained(MODEL_PATH, trust_remote_code=True)
    model_kwargs = {
        "torch_dtype": torch.float16 if device.type == "cuda" else "auto",
        "trust_remote_code": True,
    }
    if device.type == "cuda":
        model_kwargs["device_map"] = {"": str(device)}

    model = AutoModelForImageTextToText.from_pretrained(MODEL_PATH, **model_kwargs)
    if device.type == "cpu":
        model = model.to(device)
    model.eval()
    print(f"Model device: {model.device}")

    failures = 0
    for index, image_path in enumerate(image_paths, start=1):
        relative_path = image_path.relative_to(INPUT_DIR)
        output_path = (OUTPUT_DIR / relative_path).with_suffix(".txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.is_file() and output_path.stat().st_mtime >= image_path.stat().st_mtime:
            print(f"[{index}/{len(image_paths)}] Skipping existing output: {relative_path}")
            continue

        print(f"[{index}/{len(image_paths)}] Running OCR: {relative_path}")

        try:
            max_new_tokens = 512 if len(relative_path.parts) > 1 else MAX_NEW_TOKENS
            raw_output = recognize_image(image_path, processor, model, device, max_new_tokens)
        except Exception as exc:
            failures += 1
            print(f"Failed: {relative_path}: {exc}", file=sys.stderr)
            continue

        output_path.write_text(raw_output, encoding="utf-8")
        print(f"Saved: {output_path.relative_to(BASE_DIR)} ({len(raw_output)} chars)")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
