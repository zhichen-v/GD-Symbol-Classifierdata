import argparse
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from classifier_common import (
    SymbolDataset,
    create_model,
    eval_transform,
    grouped_leak_count,
    label_counts,
    load_classes,
    load_rows,
    save_json,
    set_seed,
    train_transform,
    validate_image_paths,
)


def select_device(requested: str) -> torch.device:
    if requested != "auto":
        return torch.device(requested)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def run_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    criterion,
    optimizer,
    device: torch.device,
) -> tuple[float, float]:
    model.train()
    total_loss = 0.0
    correct = 0
    total = 0
    for images, labels, _paths in loader:
        images = images.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)

        optimizer.zero_grad(set_to_none=True)
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        total_loss += float(loss.item()) * labels.size(0)
        correct += int((outputs.argmax(dim=1) == labels).sum().item())
        total += labels.size(0)
    return total_loss / max(1, total), correct / max(1, total)


@torch.inference_mode()
def evaluate_epoch(
    model: torch.nn.Module,
    loader: DataLoader,
    criterion,
    device: torch.device,
) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    for images, labels, _paths in loader:
        images = images.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)
        outputs = model(images)
        loss = criterion(outputs, labels)
        total_loss += float(loss.item()) * labels.size(0)
        correct += int((outputs.argmax(dim=1) == labels).sum().item())
        total += labels.size(0)
    return total_loss / max(1, total), correct / max(1, total)


def print_counts(name: str, rows: list[dict[str, str]]) -> None:
    counts = label_counts(rows)
    print(f"{name}: {len(rows)} rows")
    for label in sorted(counts):
        print(f"  {label}: {counts[label]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a GD symbol image classifier.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root containing category image folders.",
    )
    parser.add_argument("--splits-dir", type=Path, help="Defaults to <data-root>/splits.")
    parser.add_argument("--output-dir", type=Path, help="Defaults to <data-root>/output.")
    parser.add_argument("--model", choices=["resnet18", "mobilenet_v3_small"], default="resnet18")
    parser.add_argument("--pretrained", action="store_true", help="Use torchvision pretrained weights.")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", default="auto")
    parser.add_argument(
        "--check-data",
        action="store_true",
        help="Validate CSVs/images and load one batch, then exit without training.",
    )
    args = parser.parse_args()

    if args.epochs < 1:
        parser.error("--epochs must be at least 1.")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1.")

    data_root = args.data_root.resolve()
    splits_dir = (args.splits_dir or data_root / "splits").resolve()
    output_dir = (args.output_dir or data_root / "output").resolve()
    set_seed(args.seed)

    classes = load_classes(splits_dir / "classes.txt")
    train_rows = load_rows(splits_dir / "train.csv", classes)
    val_rows = load_rows(splits_dir / "val.csv", classes)
    all_rows = load_rows(splits_dir / "manifest.csv", classes)
    validate_image_paths(train_rows + val_rows, data_root)

    leak_count = grouped_leak_count(all_rows)
    if leak_count:
        raise ValueError(f"{leak_count} group_id values appear in multiple splits.")

    print(f"Classes: {len(classes)}")
    print_counts("train", train_rows)
    print_counts("val", val_rows)

    train_dataset = SymbolDataset(train_rows, data_root, classes, train_transform())
    val_dataset = SymbolDataset(val_rows, data_root, classes, eval_transform())
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=torch.cuda.is_available(),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=torch.cuda.is_available(),
    )

    if args.check_data:
        images, labels, paths = next(iter(train_loader))
        print(f"Batch image tensor: {tuple(images.shape)}")
        print(f"Batch labels: {tuple(labels.shape)}")
        print(f"First sample: {paths[0]} -> {classes[int(labels[0])]}")
        print("Data check passed; no training was run.")
        return 0

    device = select_device(args.device)
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"CUDA device: {torch.cuda.get_device_name(device)}")
        torch.cuda.reset_peak_memory_stats(device)
    model = create_model(args.model, len(classes), pretrained=args.pretrained).to(device)
    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    output_dir.mkdir(parents=True, exist_ok=True)
    best_accuracy = -1.0
    history = []
    start_time = time.time()
    for epoch in range(1, args.epochs + 1):
        train_loss, train_accuracy = run_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_accuracy = evaluate_epoch(model, val_loader, criterion, device)
        scheduler.step()

        record = {
            "epoch": epoch,
            "train_loss": train_loss,
            "train_accuracy": train_accuracy,
            "val_loss": val_loss,
            "val_accuracy": val_accuracy,
            "lr": scheduler.get_last_lr()[0],
        }
        history.append(record)
        print(
            f"epoch {epoch:03d}/{args.epochs}: "
            f"train_loss={train_loss:.4f} train_acc={train_accuracy:.4f} "
            f"val_loss={val_loss:.4f} val_acc={val_accuracy:.4f}"
        )

        if val_accuracy > best_accuracy:
            best_accuracy = val_accuracy
            torch.save(
                {
                    "model_name": args.model,
                    "classes": classes,
                    "state_dict": model.state_dict(),
                    "epoch": epoch,
                    "val_accuracy": val_accuracy,
                    "val_loss": val_loss,
                    "image_size": 128,
                },
                output_dir / "best.pt",
            )

    elapsed_seconds = time.time() - start_time
    cuda_peak_memory_mib = None
    if device.type == "cuda":
        cuda_peak_memory_mib = torch.cuda.max_memory_allocated(device) / 1024 / 1024
    save_json(
        output_dir / "train_metrics.json",
        {
            "model": args.model,
            "pretrained": args.pretrained,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "weight_decay": args.weight_decay,
            "seed": args.seed,
            "device": str(device),
            "cuda_peak_memory_mib": cuda_peak_memory_mib,
            "class_count": len(classes),
            "best_val_accuracy": best_accuracy,
            "elapsed_seconds": elapsed_seconds,
            "history": history,
        },
    )
    print(f"Saved best checkpoint: {output_dir / 'best.pt'}")
    print(f"Saved metrics: {output_dir / 'train_metrics.json'}")
    if cuda_peak_memory_mib is not None:
        print(f"CUDA peak memory: {cuda_peak_memory_mib:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
