import argparse
import csv
import random
from pathlib import Path


CLASSES = [
    "ANGULARITY",
    "CIRCULARITY",
    "CIRCULAR_RUNOUT",
    "CONCENTRICITY",
    "CYLINDRICITY",
    "FLATNESS",
    "PARALLELISM",
    "PERPENDICULARITY",
    "POSITION",
    "PROFILE_LINE",
    "PROFILE_SURFACE",
    "SYMMETRY",
    "TOTAL_RUNOUT",
    "UNKNOWN",
]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
FIELDS = [
    "path",
    "label",
    "source_label",
    "group_id",
    "source_index",
    "is_augmented",
    "split",
]


def parse_source(file_path: Path, label: str, category_dir: Path) -> dict[str, str]:
    stem = file_path.stem
    source_label = label
    raw_index = stem
    source_prefix = ""

    if "_" in stem:
        prefix, suffix = stem.rsplit("_", 1)
        if suffix.isdigit():
            source_label = prefix
            raw_index = suffix
            source_prefix = f"{prefix}_"

    source_index = raw_index
    is_augmented = "false"
    if raw_index.isdigit():
        numeric = int(raw_index)
        paired_index = numeric - 100
        paired_path = category_dir / f"{source_prefix}{paired_index:03d}.png"
        if numeric > 100 and paired_path.is_file():
            source_index = f"{paired_index:03d}"
            is_augmented = "true"
        else:
            source_index = f"{numeric:03d}"

    group_id = f"{label}:{source_label}:{source_index}"
    return {
        "source_label": source_label,
        "source_index": source_index,
        "is_augmented": is_augmented,
        "group_id": group_id,
    }


def collect_rows(root: Path) -> list[dict[str, str]]:
    rows = []
    for label in CLASSES:
        category_dir = root / label
        if not category_dir.is_dir():
            raise FileNotFoundError(f"Missing category folder: {category_dir}")
        for file_path in sorted(category_dir.iterdir()):
            if not file_path.is_file() or file_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            parsed = parse_source(file_path, label, category_dir)
            rows.append(
                {
                    "path": file_path.relative_to(root).as_posix(),
                    "label": label,
                    "source_label": parsed["source_label"],
                    "group_id": parsed["group_id"],
                    "source_index": parsed["source_index"],
                    "is_augmented": parsed["is_augmented"],
                    "split": "",
                }
            )
    return rows


def assign_splits(
    rows: list[dict[str, str]],
    train_ratio: float,
    val_ratio: float,
    seed: int,
) -> None:
    rng = random.Random(seed)
    groups_by_label: dict[str, list[str]] = {}
    for row in rows:
        groups_by_label.setdefault(row["label"], [])
        if row["group_id"] not in groups_by_label[row["label"]]:
            groups_by_label[row["label"]].append(row["group_id"])

    group_split = {}
    for label in CLASSES:
        groups = groups_by_label.get(label, [])
        rng.shuffle(groups)
        train_count = round(len(groups) * train_ratio)
        val_count = round(len(groups) * val_ratio)
        for index, group_id in enumerate(groups):
            if index < train_count:
                split = "train"
            elif index < train_count + val_count:
                split = "val"
            else:
                split = "test"
            group_split[group_id] = split

    for row in rows:
        row["split"] = group_split[row["group_id"]]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build grouped train/val/test splits for the GD symbol classifier."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root containing classifier category folders.",
    )
    parser.add_argument("--output-dir", type=Path, help="Defaults to <root>/splits.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    args = parser.parse_args()

    if not 0 < args.train_ratio < 1:
        parser.error("--train-ratio must be between 0 and 1.")
    if not 0 <= args.val_ratio < 1:
        parser.error("--val-ratio must be at least 0 and less than 1.")
    if args.train_ratio + args.val_ratio >= 1:
        parser.error("--train-ratio + --val-ratio must leave room for test data.")

    root = args.root.resolve()
    output_dir = (args.output_dir or root / "splits").resolve()
    rows = collect_rows(root)
    assign_splits(rows, args.train_ratio, args.val_ratio, args.seed)
    rows.sort(key=lambda row: (row["label"], row["group_id"], row["path"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "classes.txt").write_text(
        "\n".join(CLASSES) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "manifest.csv", rows)
    for split in ("train", "val", "test"):
        write_csv(
            output_dir / f"{split}.csv",
            [row for row in rows if row["split"] == split],
        )

    print(f"Saved grouped splits to: {output_dir}")
    print(f"Rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
