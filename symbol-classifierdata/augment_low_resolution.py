import argparse
import random
import tempfile
from pathlib import Path

from PIL import Image


SOURCE_COUNT = 100
OUTPUT_OFFSET = 100


def category_directories(root: Path, category: str | None) -> list[Path]:
    if category:
        folder = root / category.strip().upper()
        if not folder.is_dir():
            raise FileNotFoundError(f"Category folder not found: {folder}")
        return [folder]

    return sorted(
        folder
        for folder in root.iterdir()
        if folder.is_dir() and any(folder.glob("*.png"))
    )


def numbered_path(folder: Path, index: int) -> Path:
    return folder / f"{index:03d}.png"


def validate_inputs(categories: list[Path], overwrite: bool) -> None:
    errors = []
    for category in categories:
        missing = [
            numbered_path(category, index).name
            for index in range(1, SOURCE_COUNT + 1)
            if not numbered_path(category, index).is_file()
        ]
        collisions = [
            numbered_path(category, index + OUTPUT_OFFSET).name
            for index in range(1, SOURCE_COUNT + 1)
            if numbered_path(category, index + OUTPUT_OFFSET).exists()
        ]
        if missing:
            errors.append(
                f"{category.name}: missing {len(missing)} source images "
                f"({', '.join(missing[:5])}{'...' if len(missing) > 5 else ''})"
            )
        if collisions and not overwrite:
            errors.append(
                f"{category.name}: {len(collisions)} output images already exist; "
                "use --overwrite only if replacing them is intentional"
            )

    if errors:
        raise ValueError("Preflight failed; no images were written:\n" + "\n".join(errors))


def low_resolution_copy(
    source: Path,
    destination: Path,
    rng: random.Random,
    minimum_side: int,
    maximum_side: int,
) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGB")

    low_long_side = rng.randint(minimum_side, maximum_side)
    scale = low_long_side / max(image.size)
    low_size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    low_resolution = image.resize(low_size, Image.Resampling.LANCZOS)
    augmented = low_resolution.resize(image.size, Image.Resampling.NEAREST)
    destination.parent.mkdir(parents=True, exist_ok=True)
    augmented.save(destination)


def augment(
    root: Path,
    category: str | None,
    minimum_side: int,
    maximum_side: int,
    seed: int | None,
    overwrite: bool,
) -> None:
    if not root.is_dir():
        raise FileNotFoundError(f"Data root not found: {root}")

    categories = category_directories(root, category)
    if not categories:
        raise ValueError(f"No category folders containing PNG images found in: {root}")
    validate_inputs(categories, overwrite)

    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(dir=root, prefix=".low-resolution-tmp-") as temporary_name:
        temporary_root = Path(temporary_name)
        for category in categories:
            for index in range(1, SOURCE_COUNT + 1):
                low_resolution_copy(
                    numbered_path(category, index),
                    numbered_path(temporary_root / category.name, index + OUTPUT_OFFSET),
                    rng,
                    minimum_side,
                    maximum_side,
                )

        for category in categories:
            for index in range(1, SOURCE_COUNT + 1):
                destination = numbered_path(category, index + OUTPUT_OFFSET)
                numbered_path(
                    temporary_root / category.name,
                    index + OUTPUT_OFFSET,
                ).replace(destination)

    print(
        f"Saved {len(categories) * SOURCE_COUNT} low-resolution images "
        f"across {len(categories)} categories."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create visibly low-resolution copies of 001.png through 100.png "
            "as 101.png through 200.png in each category folder."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Folder containing category directories. Defaults to this script's directory.",
    )
    parser.add_argument(
        "--category",
        help="Process only one category folder, for example PERPENDICULARITY.",
    )
    parser.add_argument(
        "--min-side",
        type=int,
        default=20,
        help="Minimum random low-resolution longest side. Defaults to 20 pixels.",
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=48,
        help="Maximum random low-resolution longest side. Defaults to 48 pixels.",
    )
    parser.add_argument("--seed", type=int, help="Optional random seed for reproducible output.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing 101.png through 200.png files.",
    )
    args = parser.parse_args()

    if args.min_side < 2:
        parser.error("--min-side must be at least 2.")
    if args.max_side < args.min_side:
        parser.error("--max-side must be greater than or equal to --min-side.")

    augment(
        args.root.resolve(),
        args.category,
        args.min_side,
        args.max_side,
        args.seed,
        args.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
