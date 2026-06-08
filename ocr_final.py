import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import torch
from dotenv import load_dotenv
from PIL import Image, ImageOps
from transformers import AutoModelForImageTextToText, AutoProcessor


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "final-table" / "input"
OUTPUT_DIR = ROOT / "final-table" / "output"
SYMBOL_CLASSIFIER_DIR = ROOT / "symbol-classifierdata"
CLASSIFIER_CHECKPOINT = SYMBOL_CLASSIFIER_DIR / "output" / "best.pt"
BASE_MODEL = "zai-org/GLM-OCR"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

load_dotenv(ROOT / ".env")

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


def merge_close_lines(lines: list[int], minimum_gap: int = 5) -> list[int]:
    if not lines:
        return []

    merged = []
    cluster = [lines[0]]
    for line in lines[1:]:
        if line - cluster[-1] <= minimum_gap:
            cluster.append(line)
            continue
        merged.append(round(sum(cluster) / len(cluster)))
        cluster = [line]
    merged.append(round(sum(cluster) / len(cluster)))
    return merged


def detect_grid(image: Image.Image) -> tuple[list[int], list[int]]:
    grayscale = np.asarray(image.convert("L"))
    dark_pixels = grayscale < 150
    x_lines = merge_close_lines(group_line_centers(dark_pixels.mean(axis=0), minimum_coverage=0.7))
    y_lines = merge_close_lines(group_line_centers(dark_pixels.mean(axis=1), minimum_coverage=0.7))
    if len(x_lines) < 3 or len(y_lines) < 3:
        raise ValueError(
            f"Could not detect a table grid: {len(x_lines)} vertical and {len(y_lines)} horizontal lines."
        )
    return x_lines, y_lines


def crop_cell(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return Image.new("RGB", (1, 1), "white")
    inset = 2 if right - left > 4 and bottom - top > 4 else 0
    cell = image.crop((left + inset, top + inset, right - inset, bottom - inset))
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


def normalize_symbol_box(cell: Image.Image, box: tuple[int, int, int, int]) -> Image.Image | None:
    left, top, right, bottom = box
    left = max(0, int(left))
    top = max(0, int(top))
    right = min(cell.width, int(right))
    bottom = min(cell.height, int(bottom))
    if right - left < 3 or bottom - top < 3:
        return None
    return normalize_symbol_crop(cell.crop((left, top, right, bottom)))


def symbol_crop_insets(frame_height: int) -> list[tuple[int, int, int, int]]:
    edge_inset = max(1, round(frame_height * 0.08))
    left_inset = max(2, round(frame_height * 0.16))
    fallback = max(1, round(frame_height * 0.1))
    return [
        (left_inset, edge_inset, edge_inset, edge_inset),
        (fallback, fallback, fallback, fallback),
    ]


def crop_gd_symbol(cell: Image.Image) -> tuple[Image.Image, str] | None:
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
            if abs(left - overlap_left) > max(3, round(frame_height * 0.25)):
                continue
            for raw_separator in verticals[1:]:
                separator = overlap_left + raw_separator
                compartment_width = separator - left
                aspect_ratio = compartment_width / frame_height
                if not 0.4 <= aspect_ratio <= 2.2:
                    continue

                for left_inset, top_inset, right_inset, bottom_inset in symbol_crop_insets(frame_height):
                    crop_box = (
                        left + left_inset,
                        top + top_inset,
                        separator - right_inset,
                        bottom - bottom_inset,
                    )
                    if crop_box[2] - crop_box[0] < 3 or crop_box[3] - crop_box[1] < 3:
                        continue

                    normalized = normalize_symbol_box(cell, crop_box)
                    if normalized:
                        candidates.append((abs(aspect_ratio - 1.4), normalized, "closed_frame"))
                        break

    if candidates:
        best = min(candidates, key=lambda candidate: candidate[0])
        return best[1], best[2]

    return crop_gd_symbol_from_partial_frame(
        cell,
        dark,
        horizontal_lines,
        height,
        minimum_line_width,
    )


def crop_gd_frame(cell: Image.Image) -> tuple[Image.Image, dict] | None:
    grayscale = np.asarray(cell.convert("L"))
    dark = grayscale < 190
    height, width = dark.shape
    minimum_line_width = max(10, round(width * 0.08))

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
            if frame_height > min(90, round(height * 0.9)):
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
                (vertical_coverage >= 0.55) & top_touch & bottom_touch
            )
            if len(verticals) < 2:
                continue

            line_left = min(top_left, bottom_left)
            line_right = max(top_right, bottom_right)
            left = min(line_left, overlap_left + verticals[0])
            right = max(line_right, overlap_left + verticals[-1])
            frame_width = right - left
            if frame_width < max(12, round(frame_height * 1.2)):
                continue
            aspect_ratio = frame_width / frame_height
            if not 1.2 <= aspect_ratio <= 12:
                continue

            padding = max(1, round(frame_height * 0.06))
            crop_box = (
                max(0, left - padding),
                max(0, top - padding),
                min(cell.width, right + padding + 1),
                min(cell.height, bottom + padding + 1),
            )
            crop = cell.crop(crop_box).convert("RGB")
            if crop.width < 8 or crop.height < 8:
                continue
            score = (-frame_width, abs(aspect_ratio - 4.0))
            candidates.append(
                (
                    score,
                    crop,
                    {
                        "source": "closed_frame",
                        "bbox": list(crop_box),
                        "frame_vertical_count": len(verticals),
                    },
                )
            )

    if not candidates:
        return None
    _, crop, metadata = min(candidates, key=lambda candidate: candidate[0])
    return crop, metadata


def crop_gd_symbol_from_partial_frame(
    cell: Image.Image,
    dark: np.ndarray,
    horizontal_lines: list[tuple[int, int, int]],
    height: int,
    minimum_line_width: int,
) -> tuple[Image.Image, str] | None:
    candidates = []
    for top, frame_left, frame_right in horizontal_lines:
        if frame_right - frame_left + 1 < minimum_line_width:
            continue

        search_bottom = min(height, top + min(80, max(12, round(height * 0.85))))
        band = dark[top:search_bottom, frame_left : frame_right + 1]
        dark_y, _ = np.where(band)
        if not len(dark_y):
            continue

        frame_bottom = top + int(dark_y.max())
        frame_height = frame_bottom - top + 1
        if frame_height < 8 or frame_height > min(80, round(height * 0.85)):
            continue

        verticals = partial_frame_verticals(dark, top, frame_bottom, frame_left, frame_right)
        if len(verticals) < 2:
            continue

        left = verticals[0]
        if abs(left - frame_left) > max(3, round(frame_height * 0.25)):
            continue

        for separator in verticals[1:]:
            compartment_width = separator - left
            aspect_ratio = compartment_width / frame_height
            if not 0.4 <= aspect_ratio <= 2.2:
                continue

            for left_inset, top_inset, right_inset, bottom_inset in symbol_crop_insets(frame_height):
                crop_box = (
                    left + left_inset,
                    top + top_inset,
                    separator - right_inset,
                    frame_bottom - bottom_inset,
                )
                normalized = normalize_symbol_box(cell, crop_box)
                if normalized:
                    candidates.append((abs(aspect_ratio - 1.4) + 0.25, normalized, "partial_frame"))
                    break
            break

    if not candidates:
        return None
    best = min(candidates, key=lambda candidate: candidate[0])
    return best[1], best[2]


def partial_frame_verticals(
    dark: np.ndarray,
    top: int,
    bottom: int,
    left: int,
    right: int,
) -> list[int]:
    frame = dark[top : bottom + 1, left : right + 1]
    if frame.size == 0:
        return []

    vertical_coverage = frame.mean(axis=0)
    centers = true_run_centers(vertical_coverage >= 0.55)
    return [left + center for center in centers]


def classifier_guided_symbol_crop(
    cell: Image.Image,
    symbol_classifier: dict,
) -> dict | None:
    candidates = classifier_guided_symbol_candidates(cell)
    best = None
    for box in candidates:
        symbol = normalize_symbol_box(cell, box)
        if symbol is None:
            continue
        predictions = predict_symbol(symbol, symbol_classifier)
        score, positive_confidence = classifier_guided_score(predictions)
        if positive_confidence < 0.35:
            continue
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "symbol": symbol,
                "top_predictions": predictions,
                "crop_source": "classifier_guided",
            }
    return best


def classifier_guided_symbol_candidates(cell: Image.Image) -> list[tuple[int, int, int, int]]:
    grayscale = np.asarray(cell.convert("L"))
    dark = grayscale < 190
    height, width = dark.shape
    dark_y, dark_x = np.where(dark)
    if not len(dark_x):
        return []

    content_left = int(dark_x.min())
    content_top = int(dark_y.min())
    content_bottom = int(dark_y.max()) + 1
    frame_height = max(8, content_bottom - content_top)
    candidates = []
    seen = set()

    def add_box(box: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = box
        box = (
            max(0, left),
            max(0, top),
            min(width, right),
            min(height, bottom),
        )
        if box[2] - box[0] < 3 or box[3] - box[1] < 3:
            return
        key = tuple(round(value / 2) for value in box)
        if key in seen:
            return
        seen.add(key)
        candidates.append(box)

    for box in horizontal_frame_candidate_boxes(dark, height, width):
        add_box(box)

    max_symbol_width = max(10, round(frame_height * 2.2))
    x_limit = min(width, content_left + max_symbol_width)
    for x_offset in (0, -2, 2, 4):
        left = content_left + x_offset
        for y_offset in (-2, 0, 2):
            top = content_top + y_offset
            bottom = content_bottom + y_offset
            for scale in (0.9, 1.2, 1.5, 1.8, 2.1):
                right = min(x_limit, left + round(frame_height * scale))
                add_box((left, top, right, bottom))

    return candidates[:80]


def horizontal_frame_candidate_boxes(
    dark: np.ndarray,
    height: int,
    width: int,
) -> list[tuple[int, int, int, int]]:
    minimum_line_width = max(10, round(width * 0.06))
    boxes = []
    for y in range(height):
        run = longest_true_run(dark[y])
        if not run or run[1] - run[0] + 1 < minimum_line_width:
            continue
        frame_left, frame_right = run
        search_bottom = min(height, y + min(80, max(12, round(height * 0.85))))
        band = dark[y:search_bottom, frame_left : frame_right + 1]
        dark_y, _ = np.where(band)
        if not len(dark_y):
            continue
        frame_bottom = y + int(dark_y.max()) + 1
        frame_height = max(8, frame_bottom - y)
        for x_offset in (0, -2, 2):
            left = frame_left + x_offset
            for scale in (0.8, 1.1, 1.4, 1.7, 2.0):
                right = left + round(frame_height * scale)
                boxes.append((left, y, right, frame_bottom))
    return boxes


def classifier_guided_score(predictions: list[dict[str, float | str]]) -> tuple[float, float]:
    positive_confidence = 0.0
    unknown_confidence = 0.0
    for prediction in predictions:
        confidence = float(prediction["confidence"])
        label = str(prediction["label"])
        if label == "UNKNOWN":
            unknown_confidence = confidence
        elif gd_tag_for_classifier_label(label) is not None:
            positive_confidence = max(positive_confidence, confidence)
    return positive_confidence - (unknown_confidence * 0.25), positive_confidence


def predict_symbol(symbol: Image.Image, symbol_classifier: dict) -> list[dict[str, float | str]]:
    inputs = symbol_classifier["transform"](symbol).unsqueeze(0).to(symbol_classifier["device"])
    with torch.inference_mode():
        probabilities = torch.softmax(symbol_classifier["model"](inputs), dim=1)[0].cpu()

    classes = symbol_classifier["classes"]
    top_count = min(5, len(classes))
    top_values, top_indices = torch.topk(probabilities, k=top_count)
    return [
        {
            "label": classes[int(index)],
            "confidence": float(value),
        }
        for value, index in zip(top_values, top_indices)
    ]


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


def classifier_detail(predictions: list[dict[str, float | str]]) -> str:
    if not predictions:
        return ""
    top1 = predictions[0]
    if len(predictions) == 1:
        return f"{top1['label']} {top1['confidence']:.3f}"
    top2 = predictions[1]
    return (
        f"{top1['label']} {top1['confidence']:.3f}; "
        f"top2 {top2['label']} {top2['confidence']:.3f}"
    )


def classify_gd_tag(
    cell: Image.Image,
    symbol_classifier: dict,
    threshold: float,
) -> dict:
    crop_result = crop_gd_symbol(cell)
    symbol = None
    crop_source = None
    predictions = None
    if crop_result is not None:
        symbol, crop_source = crop_result
    else:
        guided = classifier_guided_symbol_crop(cell, symbol_classifier)
        if guided is not None:
            symbol = guided["symbol"]
            crop_source = guided["crop_source"]
            predictions = guided["top_predictions"]

    if symbol is None:
        return {
            "tag": None,
            "message": "symbol crop not found",
            "reason": "symbol_crop_not_found",
            "symbol": None,
            "crop_source": None,
            "top_predictions": [],
        }

    if predictions is None:
        predictions = predict_symbol(symbol, symbol_classifier)
    top1 = predictions[0]
    tag = gd_tag_for_classifier_label(str(top1["label"]))
    if crop_source != "classifier_guided" and (top1["confidence"] < threshold or tag is None):
        guided = classifier_guided_symbol_crop(cell, symbol_classifier)
        if guided is not None:
            guided_predictions = guided["top_predictions"]
            guided_top1 = guided_predictions[0]
            guided_tag = gd_tag_for_classifier_label(str(guided_top1["label"]))
            if guided_top1["confidence"] >= threshold and guided_tag is not None:
                symbol = guided["symbol"]
                crop_source = guided["crop_source"]
                predictions = guided_predictions
                top1 = guided_top1
                tag = guided_tag

    detail = classifier_detail(predictions)
    if top1["confidence"] < threshold:
        return {
            "tag": None,
            "message": f"classifier below threshold ({detail})",
            "reason": "below_threshold",
            "symbol": symbol,
            "crop_source": crop_source,
            "top_predictions": predictions,
        }

    if tag is None:
        return {
            "tag": None,
            "message": f"classifier rejected ({detail})",
            "reason": "unknown",
            "symbol": symbol,
            "crop_source": crop_source,
            "top_predictions": predictions,
        }
    return {
        "tag": tag,
        "message": detail,
        "reason": "accepted",
        "symbol": symbol,
        "crop_source": crop_source,
        "top_predictions": predictions,
    }


def write_classifier_debug(
    debug_dir: Path,
    row_number: int,
    source_image: Path,
    characteristic: str,
    specification: str,
    specification_cell: Image.Image,
    classification: dict,
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"row{row_number:03d}"
    cell_name = f"{prefix}_spec_cell.png"
    symbol_name = f"{prefix}_symbol_crop.png" if classification.get("symbol") is not None else None

    specification_cell.save(debug_dir / cell_name)
    symbol = classification.get("symbol")
    if symbol is not None:
        symbol.save(debug_dir / symbol_name)

    payload = {
        "row_number": row_number,
        "source_image": source_image.as_posix(),
        "characteristic": characteristic,
        "specification": specification,
        "reason": classification.get("reason"),
        "message": classification.get("message"),
        "crop_source": classification.get("crop_source"),
        "top_predictions": classification.get("top_predictions", []),
        "spec_cell_image": cell_name,
        "symbol_crop_image": symbol_name,
    }
    (debug_dir / f"{prefix}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_classifier_debug(debug_dir: Path) -> None:
    if not debug_dir.is_dir():
        return
    for path in debug_dir.glob("row[0-9][0-9][0-9]*"):
        if path.is_file():
            path.unlink()


def find_column(header: list[str], name: str) -> int:
    target = normalize_header_key(name)
    for index, cell in enumerate(header):
        if target in normalize_header_key(cell):
            return index
    raise ValueError(f"Required table column not found: {name}")


def resolve_table_columns(header: list[str]) -> tuple[int, int]:
    characteristic_column = find_column_or_none(header, "CHARACTERISTIC")
    specification_column = find_column_or_none(header, "SPECIFICATION")

    if characteristic_column is None and specification_column is not None and specification_column > 0:
        characteristic_column = specification_column - 1
    if specification_column is None and characteristic_column is not None and characteristic_column + 1 < len(header):
        specification_column = characteristic_column + 1

    location_column = find_column_or_none(header, "LOCATION")
    if location_column is not None:
        if characteristic_column is None and location_column + 1 < len(header):
            characteristic_column = location_column + 1
        if specification_column is None and location_column + 2 < len(header):
            specification_column = location_column + 2

    bubble_column = find_column_or_none(header, "BUBBLE")
    if bubble_column is not None:
        if characteristic_column is None and bubble_column + 2 < len(header):
            characteristic_column = bubble_column + 2
        if specification_column is None and bubble_column + 3 < len(header):
            specification_column = bubble_column + 3

    if characteristic_column is None or specification_column is None:
        if len(header) == 4:
            characteristic_column = 2 if characteristic_column is None else characteristic_column
            specification_column = 3 if specification_column is None else specification_column
        elif len(header) == 5 and not normalize_header_key(header[0]):
            characteristic_column = 3 if characteristic_column is None else characteristic_column
            specification_column = 4 if specification_column is None else specification_column

    missing = []
    if characteristic_column is None:
        missing.append("CHARACTERISTIC")
    if specification_column is None:
        missing.append("SPECIFICATION")
    if missing:
        raise ValueError(
            f"Required table column not found: {', '.join(missing)}. OCR header={header!r}"
        )

    return characteristic_column, specification_column


def resolve_table_header(results: list[list[str]]) -> tuple[int, int, int]:
    candidates = []
    for row_index, header in enumerate(results[: min(4, len(results))]):
        try:
            characteristic_column, specification_column = resolve_table_columns(header)
        except ValueError:
            candidates.append(header)
            continue
        return row_index, characteristic_column, specification_column

    raise ValueError(f"Required table header not found. OCR header candidates={candidates!r}")


def find_column_or_none(header: list[str], name: str) -> int | None:
    try:
        return find_column(header, name)
    except ValueError:
        return None


def normalize_header_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def remove_noise_rows(
    results: list[list[str]],
    cells: list[list[Image.Image | None]],
) -> tuple[list[list[str]], list[list[Image.Image | None]], int]:
    if not results:
        return results, cells, 0

    filtered_results = [results[0]]
    filtered_cells = [cells[0]]
    skipped = 0
    for row, cell_row in zip(results[1:], cells[1:]):
        if is_noise_row(row):
            skipped += 1
            continue
        filtered_results.append(row)
        filtered_cells.append(cell_row)
    return filtered_results, filtered_cells, skipped


def is_noise_row(row: list[str]) -> bool:
    text = " ".join(str(cell or "") for cell in row)
    return not bool(re.search(r"[A-Za-z0-9Øµμ°]", text))


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


def image_asset_paths(json_path: Path) -> tuple[Path, Path]:
    return (
        json_path.with_name(f"{json_path.stem}_image_assets.json"),
        json_path.with_name(f"{json_path.stem}_assets"),
    )


def clear_image_assets(manifest_path: Path, assets_dir: Path) -> None:
    manifest_path.unlink(missing_ok=True)
    if assets_dir.is_dir():
        shutil.rmtree(assets_dir)


def save_gd_frame_asset(
    assets_dir: Path,
    row_index: int,
    source_image: Path,
    specification_column: int,
    specification_cell: Image.Image,
) -> dict | None:
    crop_result = crop_gd_frame(specification_cell)
    if crop_result is None:
        return None

    crop, metadata = crop_result
    assets_dir.mkdir(parents=True, exist_ok=True)
    image_name = f"row{row_index + 1:03d}_gd_frame.png"
    image_path = assets_dir / image_name
    crop.save(image_path)

    return {
        "visual_index": row_index,
        "row_number": row_index + 1,
        "column": specification_column,
        "kind": "gd_frame",
        "path": image_path.name,
        "directory": assets_dir.name,
        "width": crop.width,
        "height": crop.height,
        "source_image": source_image.as_posix(),
        **metadata,
    }


def write_image_asset_manifest(manifest_path: Path, source_image: Path, assets: list[dict]) -> None:
    payload = {
        "source_image": source_image.as_posix(),
        "assets": assets,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    classifier_debug_dir = json_path.with_name(f"{json_path.stem}_classifier_debug")
    image_manifest_path, image_assets_dir = image_asset_paths(json_path)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    if (
        markdown_path.is_file()
        and json_path.is_file()
        and markdown_path.stat().st_mtime >= image_path.stat().st_mtime
        and json_path.stat().st_mtime >= image_path.stat().st_mtime
    ):
        print(f"Skipping existing output: {relative_path}")
        return

    clear_classifier_debug(classifier_debug_dir)
    clear_image_assets(image_manifest_path, image_assets_dir)

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
    header_row_index, characteristic_column, specification_column = resolve_table_header(results)
    if header_row_index:
        print(f"  Header detected at row {header_row_index + 1}; skipped {header_row_index} leading noise row(s)")
        results = results[header_row_index:]
        cells = cells[header_row_index:]
    results, cells, skipped_rows = remove_noise_rows(results, cells)
    if skipped_rows:
        print(f"  Skipped {skipped_rows} noise row(s)")
    image_assets = []
    for row_index in range(1, len(results)):
        characteristic = results[row_index][characteristic_column]
        results[row_index][specification_column] = apply_diameter_marker(
            characteristic,
            normalize_diameter_symbols(results[row_index][specification_column]),
        )
        if not is_gd_characteristic(characteristic):
            continue
        specification_cell = cells[row_index][specification_column]
        if specification_cell:
            asset = save_gd_frame_asset(
                image_assets_dir,
                row_index,
                relative_path,
                specification_column,
                specification_cell,
            )
            if asset:
                image_assets.append(asset)
                print(f"  Row {row_index + 1}: saved GD frame image asset")
                continue
        results[row_index][specification_column] = (
            f"{GD_REVIEW_TAG} {results[row_index][specification_column]}".strip()
        )
        print(f"  Row {row_index + 1}: GD frame image asset not found")
        results[row_index][specification_column] = normalize_diameter_symbols(
            results[row_index][specification_column]
        )

    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_image_asset_manifest(image_manifest_path, relative_path, image_assets)
    write_markdown(results, markdown_path)
    print(f"Saved: {markdown_path.relative_to(ROOT)}")
    print(f"Saved: {json_path.relative_to(ROOT)}")
    print(f"Saved: {image_manifest_path.relative_to(ROOT)}")


def output_is_current(image_path: Path) -> bool:
    relative_path = image_path.relative_to(INPUT_DIR)
    markdown_path = (OUTPUT_DIR / relative_path).with_suffix(".md")
    json_path = (OUTPUT_DIR / relative_path).with_suffix(".json")
    image_manifest_path, _ = image_asset_paths(json_path)
    return (
        markdown_path.is_file()
        and json_path.is_file()
        and image_manifest_path.is_file()
        and markdown_path.stat().st_mtime >= image_path.stat().st_mtime
        and json_path.stat().st_mtime >= image_path.stat().st_mtime
        and image_manifest_path.stat().st_mtime >= image_path.stat().st_mtime
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full-table OCR with a GD&T symbol classifier.")
    parser.add_argument("--force", action="store_true", help="Regenerate outputs even when they are newer than input.")
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument(
        "--download-base-model",
        action="store_true",
        help="Allow downloading GLM-OCR from Hugging Face when it is not already cached locally.",
    )
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
            json_path = (OUTPUT_DIR / relative_path).with_suffix(".json")
            json_path.unlink(missing_ok=True)
            image_manifest_path, image_assets_dir = image_asset_paths(json_path)
            clear_image_assets(image_manifest_path, image_assets_dir)

    pending_images = [image_path for image_path in image_paths if not output_is_current(image_path)]
    if not pending_images:
        print("All final-table outputs are up to date.")
        return 0

    symbol_classifier = None
    if not args.disable_symbol_classifier:
        print("GD image passthrough mode: symbol classifier is skipped.")

    local_files_only = not args.download_base_model
    if local_files_only:
        print("Loading base model from local cache...")
    else:
        print("Loading base model; downloads are allowed if the cache is missing...")
    processor = AutoProcessor.from_pretrained(BASE_MODEL, local_files_only=local_files_only)
    model = AutoModelForImageTextToText.from_pretrained(
        BASE_MODEL,
        dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=local_files_only,
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
