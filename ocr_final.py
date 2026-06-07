import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps
from transformers import AutoModelForImageTextToText, AutoProcessor


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "final-table" / "input"
OUTPUT_DIR = ROOT / "final-table" / "output"
SYMBOL_CLASSIFIER_DIR = ROOT / "symbol-classifierdata"
CLASSIFIER_CHECKPOINT = SYMBOL_CLASSIFIER_DIR / "output" / "best.pt"
BASE_MODEL = "zai-org/GLM-OCR"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

if str(SYMBOL_CLASSIFIER_DIR) not in sys.path:
    sys.path.insert(0, str(SYMBOL_CLASSIFIER_DIR))
from classifier_common import create_model, eval_transform

GD_CHARACTERISTICS = {
    "ANGULARITY",
    "CIRCULAR RUNOUT",
    "CIRCULARITY",
    "CONCENTRICITY",
    "CYLINDRICITY",
    "FLATNESS",
    "GD",
    "GD&T",
    "GEOMETRIC TOLERANCE",
    "PARALLELISM",
    "PERPENDICULARITY",
    "POSITION",
    "PROFILE LINE",
    "PROFILE SURFACE",
    "SYMMETRY",
    "TOTAL RUNOUT",
    "TRUE POSITION",
}
GENERIC_GD_CHARACTERISTICS = {"GD", "GD&T", "GEOMETRIC TOLERANCE"}
GD_REVIEW_TAG = "[GD_REVIEW_REQUIRED]"
DIAMETER_SYMBOL_PATTERN = re.compile(r"(?i)\\(?:varnothing|diameter|oslash)|[⌀∅ø]")
EXPECTED_GD_TAGS = {
    "ANGULARITY": "[GD_ANGULARITY]",
    "CIRCULAR RUNOUT": "[GD_CIRCULAR_RUNOUT]",
    "CIRCULARITY": "[GD_CIRCULARITY]",
    "CONCENTRICITY": "[GD_CONCENTRICITY]",
    "CYLINDRICITY": "[GD_CYLINDRICITY]",
    "FLATNESS": "[GD_FLATNESS]",
    "PARALLELISM": "[GD_PARALLELISM]",
    "PERPENDICULARITY": "[GD_PERPENDICULARITY]",
    "POSITION": "[GD_POSITION]",
    "PROFILE LINE": "[GD_PROFILE_LINE]",
    "PROFILE SURFACE": "[GD_PROFILE_SURFACE]",
    "SYMMETRY": "[GD_SYMMETRY]",
    "TOTAL RUNOUT": "[GD_TOTAL_RUNOUT]",
    "TRUE POSITION": "[GD_POSITION]",
}


def group_line_centers(values: np.ndarray, minimum_coverage: float) -> list[int]:
    indices = np.where(values >= minimum_coverage)[0]
    if not len(indices):
        return []

    groups = []
    start = previous = int(indices[0])
    for raw_index in indices[1:]:
        index = int(raw_index)
        if index > previous + 1:
            groups.append(round((start + previous) / 2))
            start = index
        previous = index
    groups.append(round((start + previous) / 2))
    return groups


def detect_grid(image: Image.Image) -> tuple[list[int], list[int]]:
    grayscale = np.asarray(image.convert("L"))
    dark_pixels = grayscale < 150
    x_lines = group_line_centers(dark_pixels.mean(axis=0), minimum_coverage=0.7)
    y_lines = group_line_centers(dark_pixels.mean(axis=1), minimum_coverage=0.7)
    if len(x_lines) < 3 or len(y_lines) < 3:
        raise ValueError(
            f"Could not detect a table grid: {len(x_lines)} vertical and {len(y_lines)} horizontal lines."
        )
    return x_lines, y_lines


def crop_cell(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = box
    cell = image.crop((left + 2, top + 2, right - 2, bottom - 2))
    return ImageOps.expand(cell, border=8, fill="white")


def true_run_centers(values: np.ndarray) -> list[int]:
    indices = np.flatnonzero(values)
    if not len(indices):
        return []

    centers = []
    start = previous = int(indices[0])
    for raw_index in indices[1:]:
        index = int(raw_index)
        if index > previous + 1:
            centers.append(round((start + previous) / 2))
            start = index
        previous = index
    centers.append(round((start + previous) / 2))
    return centers


def longest_true_run(values: np.ndarray) -> tuple[int, int] | None:
    indices = np.flatnonzero(values)
    if not len(indices):
        return None

    best = (int(indices[0]), int(indices[0]))
    start = previous = int(indices[0])
    for raw_index in indices[1:]:
        index = int(raw_index)
        if index > previous + 1:
            if previous - start > best[1] - best[0]:
                best = (start, previous)
            start = index
        previous = index
    if previous - start > best[1] - best[0]:
        best = (start, previous)
    return best


def normalize_symbol_crop(symbol: Image.Image, size: int = 128) -> Image.Image | None:
    grayscale = np.asarray(symbol.convert("L"))
    dark_y, dark_x = np.where(grayscale < 190)
    if not len(dark_x):
        return None

    left, right = int(dark_x.min()), int(dark_x.max()) + 1
    top, bottom = int(dark_y.min()), int(dark_y.max()) + 1
    content = symbol.crop((left, top, right, bottom)).convert("RGB")
    if content.width < 2 or content.height < 2:
        return None

    target = round(size * 0.7)
    scale = min(target / content.width, target / content.height)
    resized = content.resize(
        (max(1, round(content.width * scale)), max(1, round(content.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    return canvas


def crop_gd_symbol(cell: Image.Image) -> Image.Image | None:
    grayscale = np.asarray(cell.convert("L"))
    dark = grayscale < 190
    height, width = dark.shape
    minimum_line_width = max(10, round(width * 0.06))

    horizontal_lines = []
    for y in range(height):
        run = longest_true_run(dark[y])
        if run and run[1] - run[0] + 1 >= minimum_line_width:
            horizontal_lines.append((y, run[0], run[1]))

    candidates = []
    for top_index, (top, top_left, top_right) in enumerate(horizontal_lines):
        for bottom, bottom_left, bottom_right in horizontal_lines[top_index + 1 :]:
            frame_height = bottom - top
            if frame_height < 8:
                continue
            if frame_height > min(80, round(height * 0.85)):
                break

            overlap_left = max(top_left, bottom_left)
            overlap_right = min(top_right, bottom_right)
            if overlap_right - overlap_left + 1 < minimum_line_width:
                continue

            frame = dark[top : bottom + 1, overlap_left : overlap_right + 1]
            vertical_coverage = frame.mean(axis=0)
            top_touch = frame[: min(3, frame.shape[0])].any(axis=0)
            bottom_touch = frame[-min(3, frame.shape[0]) :].any(axis=0)
            verticals = true_run_centers(
                (vertical_coverage >= 0.65) & top_touch & bottom_touch
            )
            if len(verticals) < 3:
                continue

            left = overlap_left + verticals[0]
            for raw_separator in verticals[1:]:
                separator = overlap_left + raw_separator
                compartment_width = separator - left
                aspect_ratio = compartment_width / frame_height
                if not 0.4 <= aspect_ratio <= 2.2:
                    continue

                border = max(1, round(frame_height * 0.1))
                crop_box = (
                    left + border,
                    top + border,
                    separator - border,
                    bottom - border,
                )
                if crop_box[2] - crop_box[0] < 3 or crop_box[3] - crop_box[1] < 3:
                    continue

                normalized = normalize_symbol_crop(cell.crop(crop_box))
                if normalized:
                    candidates.append((abs(aspect_ratio - 1.4), normalized))

    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]


def has_text(cell: Image.Image) -> bool:
    grayscale = np.asarray(cell.convert("L"))
    return bool((grayscale < 100).mean() >= 0.003)


def recognize(cell: Image.Image, processor, model, max_new_tokens: int) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": cell},
                {"type": "text", "text": "Text Recognition:"},
            ],
        }
    ]
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    ).to(model.device)
    inputs.pop("token_type_ids", None)

    with torch.inference_mode():
        generated_ids = model.generate(**inputs, max_new_tokens=max_new_tokens)
    return processor.decode(
        generated_ids[0][inputs["input_ids"].shape[1] :],
        skip_special_tokens=True,
    ).strip()


def recognize_base(cell: Image.Image, processor, model, max_new_tokens: int) -> str:
    return recognize(cell, processor, model, max_new_tokens)


def classifier_device(requested: str) -> torch.device:
    if requested != "auto":
        return torch.device(requested)
    return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


def gd_tag_for_classifier_label(label: str) -> str | None:
    if label == "UNKNOWN":
        return None
    return EXPECTED_GD_TAGS.get(label.replace("_", " "))


def load_symbol_classifier(checkpoint_path: Path, requested_device: str) -> dict:
    device = classifier_device(requested_device)
    checkpoint = torch.load(checkpoint_path, map_location=device)
    classes = checkpoint.get("classes")
    if not isinstance(classes, list) or "UNKNOWN" not in classes:
        raise ValueError("Classifier checkpoint must contain a classes list with UNKNOWN.")

    unsupported = [
        class_name
        for class_name in classes
        if class_name != "UNKNOWN" and gd_tag_for_classifier_label(class_name) is None
    ]
    if unsupported:
        raise ValueError(f"Classifier checkpoint contains unsupported labels: {unsupported}")

    model = create_model(checkpoint["model_name"], len(classes), pretrained=False).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return {
        "model": model,
        "classes": classes,
        "device": device,
        "transform": eval_transform(),
    }


def classify_gd_tag(
    cell: Image.Image,
    symbol_classifier: dict,
    threshold: float,
) -> tuple[str | None, str]:
    symbol = crop_gd_symbol(cell)
    if symbol is None:
        return None, "symbol crop not found"

    inputs = symbol_classifier["transform"](symbol).unsqueeze(0).to(symbol_classifier["device"])
    with torch.inference_mode():
        probabilities = torch.softmax(symbol_classifier["model"](inputs), dim=1)[0].cpu()

    classes = symbol_classifier["classes"]
    top_count = min(2, len(classes))
    top_values, top_indices = torch.topk(probabilities, k=top_count)
    top1_label = classes[int(top_indices[0])]
    top1_confidence = float(top_values[0])
    top2_label = classes[int(top_indices[1])] if top_count > 1 else ""
    top2_confidence = float(top_values[1]) if top_count > 1 else 0.0
    detail = (
        f"{top1_label} {top1_confidence:.3f}; "
        f"top2 {top2_label} {top2_confidence:.3f}"
    )

    if top1_confidence < threshold:
        return None, f"classifier below threshold ({detail})"

    tag = gd_tag_for_classifier_label(top1_label)
    if tag is None:
        return None, f"classifier rejected ({detail})"
    return tag, detail


def find_column(header: list[str], name: str) -> int:
    target = name.upper()
    for index, cell in enumerate(header):
        if target in cell.upper():
            return index
    raise ValueError(f"Required table column not found: {name}")


def is_gd_characteristic(text: str) -> bool:
    normalized = " ".join(text.upper().replace("_", " ").split())
    return any(keyword == normalized or keyword in normalized for keyword in GD_CHARACTERISTICS)


def normalized_characteristic(text: str) -> str:
    return " ".join(text.upper().replace("_", " ").split())


def normalize_diameter_symbols(text: str) -> str:
    normalized = DIAMETER_SYMBOL_PATTERN.sub("Ø", text)
    return re.sub(r"\$\s*Ø\s*\$", "Ø", normalized)


def apply_diameter_marker(characteristic: str, specification: str) -> str:
    if normalized_characteristic(characteristic) != "DIAMETER":
        return specification
    if "Ø" in specification or not re.search(r"\d", specification):
        return specification
    return f"Ø{specification.lstrip()}"


def apply_expected_gd_tag(characteristic: str, specification: str) -> str:
    normalized = normalized_characteristic(characteristic)
    expected = EXPECTED_GD_TAGS.get(normalized)
    if not expected:
        return specification
    if re.match(r"^\[GD_[A-Z_]+\]", specification):
        return re.sub(r"^\[GD_[A-Z_]+\]", expected, specification, count=1)
    return f"{expected} {specification}".strip()


def apply_classified_gd_tag(tag: str, specification: str) -> str:
    specification = re.sub(r"^\[GD_[A-Z_]+\]\s*", "", specification).strip()
    return f"{tag} {specification}".strip()


def markdown_cell(text: str) -> str:
    return text.replace("|", r"\|").replace("\r", "").replace("\n", "<br>")


def write_markdown(rows: list[list[str]], output_path: Path) -> None:
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        "| " + " | ".join(markdown_cell(cell) for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend(
        "| " + " | ".join(markdown_cell(cell) for cell in row) + " |"
        for row in normalized[1:]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_image(
    image_path: Path,
    processor,
    model,
    max_new_tokens: int,
    symbol_classifier: dict | None,
    classifier_threshold: float,
) -> None:
    relative_path = image_path.relative_to(INPUT_DIR)
    markdown_path = (OUTPUT_DIR / relative_path).with_suffix(".md")
    json_path = (OUTPUT_DIR / relative_path).with_suffix(".json")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    if (
        markdown_path.is_file()
        and json_path.is_file()
        and markdown_path.stat().st_mtime >= image_path.stat().st_mtime
        and json_path.stat().st_mtime >= image_path.stat().st_mtime
    ):
        print(f"Skipping existing output: {relative_path}")
        return

    with Image.open(image_path) as source:
        image = source.convert("RGB")
    x_lines, y_lines = detect_grid(image)
    print(f"Detected {len(y_lines) - 1} rows and {len(x_lines) - 1} columns: {relative_path}")

    cells = []
    for top, bottom in zip(y_lines, y_lines[1:]):
        row = []
        for left, right in zip(x_lines, x_lines[1:]):
            cell = crop_cell(image, (left, top, right, bottom))
            row.append(cell if has_text(cell) else None)
        cells.append(row)

    results = [
        [
            normalize_diameter_symbols(recognize_base(cell, processor, model, max_new_tokens))
            if cell
            else ""
            for cell in row
        ]
        for row in cells
    ]
    characteristic_column = find_column(results[0], "CHARACTERISTIC")
    specification_column = find_column(results[0], "SPECIFICATION")
    for row_index in range(1, len(results)):
        characteristic = results[row_index][characteristic_column]
        normalized = normalized_characteristic(characteristic)
        results[row_index][specification_column] = apply_diameter_marker(
            characteristic,
            normalize_diameter_symbols(results[row_index][specification_column]),
        )
        if not is_gd_characteristic(characteristic):
            continue
        specification_cell = cells[row_index][specification_column]
        if normalized in EXPECTED_GD_TAGS:
            results[row_index][specification_column] = apply_expected_gd_tag(
                characteristic,
                results[row_index][specification_column],
            )
        elif normalized in GENERIC_GD_CHARACTERISTICS and symbol_classifier and specification_cell:
            tag, response = classify_gd_tag(
                specification_cell,
                symbol_classifier,
                classifier_threshold,
            )
            if tag:
                results[row_index][specification_column] = apply_classified_gd_tag(
                    tag,
                    results[row_index][specification_column],
                )
                print(f"  Row {row_index + 1}: classified {tag} from symbol classifier ({response})")
            else:
                results[row_index][specification_column] = (
                    f"{GD_REVIEW_TAG} {results[row_index][specification_column]}".strip()
                )
                print(f"  Row {row_index + 1}: symbol classifier rejected ({response})")
        elif normalized in GENERIC_GD_CHARACTERISTICS:
            results[row_index][specification_column] = (
                f"{GD_REVIEW_TAG} {results[row_index][specification_column]}".strip()
            )
        results[row_index][specification_column] = normalize_diameter_symbols(
            results[row_index][specification_column]
        )

    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(results, markdown_path)
    print(f"Saved: {markdown_path.relative_to(ROOT)}")
    print(f"Saved: {json_path.relative_to(ROOT)}")


def output_is_current(image_path: Path) -> bool:
    relative_path = image_path.relative_to(INPUT_DIR)
    markdown_path = (OUTPUT_DIR / relative_path).with_suffix(".md")
    json_path = (OUTPUT_DIR / relative_path).with_suffix(".json")
    return (
        markdown_path.is_file()
        and json_path.is_file()
        and markdown_path.stat().st_mtime >= image_path.stat().st_mtime
        and json_path.stat().st_mtime >= image_path.stat().st_mtime
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full-table OCR with a GD&T symbol classifier.")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs even when they are newer than input.")
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--symbol-classifier-checkpoint", type=Path, default=CLASSIFIER_CHECKPOINT)
    parser.add_argument("--symbol-classifier-threshold", type=float, default=0.90)
    parser.add_argument("--symbol-classifier-device", default="auto")
    parser.add_argument(
        "--disable-symbol-classifier",
        action="store_true",
        help="Do not use the dedicated GD symbol classifier; generic GD/GD&T rows remain review-required.",
    )
    args = parser.parse_args()

    if not 0 <= args.symbol_classifier_threshold <= 1:
        parser.error("--symbol-classifier-threshold must be between 0 and 1.")
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        path
        for path in INPUT_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not image_paths:
        print(f"No images found in: {INPUT_DIR}")
        return 0

    if args.force:
        for image_path in image_paths:
            relative_path = image_path.relative_to(INPUT_DIR)
            (OUTPUT_DIR / relative_path).with_suffix(".md").unlink(missing_ok=True)
            (OUTPUT_DIR / relative_path).with_suffix(".json").unlink(missing_ok=True)

    pending_images = [image_path for image_path in image_paths if not output_is_current(image_path)]
    if not pending_images:
        print("All final-table outputs are up to date.")
        return 0

    symbol_classifier = None
    if not args.disable_symbol_classifier:
        checkpoint_path = args.symbol_classifier_checkpoint.resolve()
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"Symbol classifier checkpoint not found: {checkpoint_path}")
        print(f"Loading GD symbol classifier: {checkpoint_path}")
        symbol_classifier = load_symbol_classifier(checkpoint_path, args.symbol_classifier_device)

    print("Loading base model...")
    processor = AutoProcessor.from_pretrained(BASE_MODEL, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        BASE_MODEL,
        dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=True,
    )
    model.eval()

    failures = 0
    for index, image_path in enumerate(pending_images, start=1):
        print(f"[{index}/{len(pending_images)}] Processing: {image_path.relative_to(INPUT_DIR)}")
        try:
            process_image(
                image_path,
                processor,
                model,
                args.max_new_tokens,
                symbol_classifier,
                args.symbol_classifier_threshold,
            )
        except Exception as exc:
            failures += 1
            print(f"Failed: {image_path.relative_to(INPUT_DIR)}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
