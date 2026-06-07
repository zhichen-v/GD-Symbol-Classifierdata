import argparse
from collections import Counter
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from classifier_common import (
    SymbolDataset,
    create_model,
    eval_transform,
    load_classes,
    load_rows,
    save_json,
    validate_image_paths,
    write_csv,
)


def select_device(requested: str) -> torch.device:
    if requested != "auto":
        return torch.device(requested)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def empty_matrix(size: int) -> list[list[int]]:
    return [[0 for _ in range(size)] for _ in range(size)]


def matrix_rows(matrix: list[list[int]], classes: list[str]) -> list[dict[str, object]]:
    rows = []
    for true_index, true_label in enumerate(classes):
        row: dict[str, object] = {"true_label": true_label}
        for pred_index, pred_label in enumerate(classes):
            row[pred_label] = matrix[true_index][pred_index]
        rows.append(row)
    return rows


def per_class_metrics(matrix: list[list[int]], classes: list[str]) -> list[dict[str, object]]:
    rows = []
    for index, label in enumerate(classes):
        true_positive = matrix[index][index]
        actual = sum(matrix[index])
        predicted = sum(row[index] for row in matrix)
        recall = true_positive / actual if actual else 0.0
        precision = true_positive / predicted if predicted else 0.0
        rows.append(
            {
                "label": label,
                "actual": actual,
                "predicted": predicted,
                "precision": precision,
                "recall": recall,
            }
        )
    return rows


@torch.inference_mode()
def predict(
    model: torch.nn.Module,
    loader: DataLoader,
    classes: list[str],
    threshold: float,
    device: torch.device,
) -> tuple[list[dict[str, object]], dict]:
    class_to_index = {class_name: index for index, class_name in enumerate(classes)}
    unknown_index = class_to_index.get("UNKNOWN")
    if unknown_index is None:
        raise ValueError("classes.txt must include UNKNOWN for gated evaluation.")

    raw_matrix = empty_matrix(len(classes))
    gated_matrix = empty_matrix(len(classes))
    predictions = []
    accepted = 0
    accepted_correct = 0
    total = 0
    raw_correct = 0
    gated_correct = 0
    rejected = 0
    top1_counts: Counter[str] = Counter()

    model.eval()
    for images, labels, paths in loader:
        images = images.to(device, non_blocking=True)
        outputs = model(images)
        probabilities = torch.softmax(outputs, dim=1).cpu()
        top_values, top_indices = torch.topk(probabilities, k=2, dim=1)

        for row_index, true_tensor in enumerate(labels):
            true_index = int(true_tensor.item())
            true_label = classes[true_index]
            top1_index = int(top_indices[row_index, 0].item())
            top2_index = int(top_indices[row_index, 1].item())
            top1_label = classes[top1_index]
            top2_label = classes[top2_index]
            top1_confidence = float(top_values[row_index, 0].item())
            top2_confidence = float(top_values[row_index, 1].item())
            gated_index = top1_index if top1_confidence >= threshold else unknown_index
            gated_label = classes[gated_index]
            is_accepted = top1_confidence >= threshold

            raw_matrix[true_index][top1_index] += 1
            gated_matrix[true_index][gated_index] += 1
            raw_is_correct = top1_index == true_index
            gated_is_correct = gated_index == true_index
            raw_correct += int(raw_is_correct)
            gated_correct += int(gated_is_correct)
            accepted += int(is_accepted)
            rejected += int(not is_accepted)
            accepted_correct += int(is_accepted and raw_is_correct)
            total += 1
            top1_counts[top1_label] += 1

            predictions.append(
                {
                    "path": paths[row_index],
                    "label": true_label,
                    "top1": top1_label,
                    "top1_confidence": top1_confidence,
                    "top2": top2_label,
                    "top2_confidence": top2_confidence,
                    "gated_prediction": gated_label,
                    "accepted": str(is_accepted).lower(),
                    "raw_correct": str(raw_is_correct).lower(),
                    "gated_correct": str(gated_is_correct).lower(),
                }
            )

    metrics = {
        "total": total,
        "threshold": threshold,
        "raw_accuracy": raw_correct / total if total else 0.0,
        "gated_accuracy": gated_correct / total if total else 0.0,
        "coverage": accepted / total if total else 0.0,
        "accepted_accuracy": accepted_correct / accepted if accepted else 0.0,
        "rejected": rejected,
        "raw_per_class": per_class_metrics(raw_matrix, classes),
        "gated_per_class": per_class_metrics(gated_matrix, classes),
        "top1_counts": dict(sorted(top1_counts.items())),
    }
    return predictions, {
        "metrics": metrics,
        "raw_matrix": raw_matrix,
        "gated_matrix": gated_matrix,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a trained GD symbol classifier.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root containing category image folders.",
    )
    parser.add_argument("--split", type=Path, help="Defaults to <data-root>/splits/test.csv.")
    parser.add_argument("--classes", type=Path, help="Defaults to <data-root>/splits/classes.txt.")
    parser.add_argument("--checkpoint", type=Path, help="Defaults to <data-root>/output/best.pt.")
    parser.add_argument("--output-dir", type=Path, help="Defaults to <data-root>/output/evaluation.")
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1.")

    data_root = args.data_root.resolve()
    classes_path = (args.classes or data_root / "splits" / "classes.txt").resolve()
    split_path = (args.split or data_root / "splits" / "test.csv").resolve()
    checkpoint_path = (args.checkpoint or data_root / "output" / "best.pt").resolve()
    output_dir = (args.output_dir or data_root / "output" / "evaluation").resolve()

    classes = load_classes(classes_path)
    rows = load_rows(split_path, classes)
    validate_image_paths(rows, data_root)

    device = select_device(args.device)
    checkpoint = torch.load(checkpoint_path, map_location=device)
    checkpoint_classes = checkpoint.get("classes")
    if checkpoint_classes != classes:
        raise ValueError("Checkpoint classes do not match classes.txt.")
    model = create_model(checkpoint["model_name"], len(classes), pretrained=False).to(device)
    model.load_state_dict(checkpoint["state_dict"])

    dataset = SymbolDataset(rows, data_root, classes, eval_transform())
    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=torch.cuda.is_available(),
    )
    predictions, result = predict(model, loader, classes, args.threshold, device)

    output_dir.mkdir(parents=True, exist_ok=True)
    save_json(output_dir / "metrics.json", result["metrics"])
    write_csv(
        output_dir / "predictions.csv",
        predictions,
        [
            "path",
            "label",
            "top1",
            "top1_confidence",
            "top2",
            "top2_confidence",
            "gated_prediction",
            "accepted",
            "raw_correct",
            "gated_correct",
        ],
    )
    matrix_fieldnames = ["true_label"] + classes
    write_csv(
        output_dir / "confusion_matrix_raw.csv",
        matrix_rows(result["raw_matrix"], classes),
        matrix_fieldnames,
    )
    write_csv(
        output_dir / "confusion_matrix_gated.csv",
        matrix_rows(result["gated_matrix"], classes),
        matrix_fieldnames,
    )

    metrics = result["metrics"]
    print(f"Rows: {metrics['total']}")
    print(f"Raw accuracy: {metrics['raw_accuracy']:.4f}")
    print(f"Gated accuracy: {metrics['gated_accuracy']:.4f}")
    print(f"Coverage: {metrics['coverage']:.4f}")
    print(f"Accepted accuracy: {metrics['accepted_accuracy']:.4f}")
    print(f"Rejected: {metrics['rejected']}")
    print(f"Saved evaluation: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
