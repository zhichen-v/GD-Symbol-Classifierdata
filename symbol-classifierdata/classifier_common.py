import csv
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset
from torchvision import models, transforms


IMAGE_SIZE = 128
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


class SymbolDataset(Dataset):
    def __init__(
        self,
        rows: list[dict[str, str]],
        data_root: Path,
        classes: list[str],
        transform,
    ) -> None:
        self.rows = rows
        self.data_root = data_root
        self.classes = classes
        self.class_to_index = {class_name: index for index, class_name in enumerate(classes)}
        self.transform = transform

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        row = self.rows[index]
        image_path = self.data_root / row["path"]
        with Image.open(image_path) as opened:
            image = opened.convert("RGB")
        label = self.class_to_index[row["label"]]
        return self.transform(image), label, row["path"]


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_classes(path: Path) -> list[str]:
    classes = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    classes = [class_name for class_name in classes if class_name]
    if not classes:
        raise ValueError(f"No classes found in: {path}")
    if len(classes) != len(set(classes)):
        raise ValueError(f"Duplicate classes found in: {path}")
    return classes


def load_rows(csv_path: Path, classes: list[str]) -> list[dict[str, str]]:
    class_set = set(classes)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"path", "label", "group_id", "split"}
    missing = required.difference(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"{csv_path} is missing columns: {', '.join(sorted(missing))}")
    unknown_labels = sorted({row["label"] for row in rows}.difference(class_set))
    if unknown_labels:
        raise ValueError(f"{csv_path} contains labels not listed in classes.txt: {unknown_labels}")
    return rows


def validate_image_paths(rows: list[dict[str, str]], data_root: Path) -> None:
    missing = []
    bad_extensions = []
    for row in rows:
        path = data_root / row["path"]
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            bad_extensions.append(row["path"])
        if not path.is_file():
            missing.append(row["path"])
    if missing:
        details = ", ".join(missing[:5])
        raise FileNotFoundError(f"{len(missing)} images are missing under {data_root}: {details}")
    if bad_extensions:
        details = ", ".join(bad_extensions[:5])
        raise ValueError(f"{len(bad_extensions)} rows use unsupported image extensions: {details}")


def grouped_leak_count(rows: list[dict[str, str]]) -> int:
    splits_by_group: dict[str, set[str]] = {}
    for row in rows:
        splits_by_group.setdefault(row["group_id"], set()).add(row["split"])
    return sum(1 for splits in splits_by_group.values() if len(splits) > 1)


def label_counts(rows: list[dict[str, str]]) -> Counter:
    return Counter(row["label"] for row in rows)


def train_transform() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
            transforms.RandomAffine(
                degrees=5,
                translate=(0.05, 0.05),
                scale=(0.9, 1.1),
                shear=2,
                fill=255,
            ),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


def eval_transform() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


def create_model(model_name: str, num_classes: int, pretrained: bool = False) -> torch.nn.Module:
    normalized_name = model_name.lower()
    if normalized_name == "resnet18":
        weights = models.ResNet18_Weights.DEFAULT if pretrained else None
        model = models.resnet18(weights=weights)
        model.fc = torch.nn.Linear(model.fc.in_features, num_classes)
        return model
    if normalized_name == "mobilenet_v3_small":
        weights = models.MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_small(weights=weights)
        in_features = model.classifier[-1].in_features
        model.classifier[-1] = torch.nn.Linear(in_features, num_classes)
        return model
    raise ValueError(f"Unsupported model: {model_name}")


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
