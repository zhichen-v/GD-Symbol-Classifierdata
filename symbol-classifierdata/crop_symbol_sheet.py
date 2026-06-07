import argparse
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# .\.venv-finetune\Scripts\python.exe .\symbol-classifierdata\crop_symbol_sheet.py "C:\Users\爸爸\Desktop\model\symbol-classifierdata\1.png" DIAMETER --top-trim-ratio 0.05
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def category_name(value: str) -> str:
    normalized = value.strip().upper().replace(" ", "_")
    if not normalized:
        raise argparse.ArgumentTypeError("Category cannot be empty.")
    return normalized


def group_line_centers(values: np.ndarray, minimum_coverage: float) -> list[int]:
    indices = np.flatnonzero(values >= minimum_coverage)
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


def detect_grid_edges(
    sheet: Image.Image,
    rows: int | None,
    columns: int | None,
    grid_threshold: int,
    grid_coverage: float,
) -> tuple[list[int], list[int]]:
    grayscale = np.asarray(sheet.convert("L"))
    attempts = []
    candidates = []
    for threshold in range(grid_threshold, 255):
        grid_pixels = grayscale < threshold
        x_edges = group_line_centers(grid_pixels.mean(axis=0), grid_coverage)
        y_edges = group_line_centers(grid_pixels.mean(axis=1), grid_coverage)
        attempts.append((threshold, len(x_edges), len(y_edges)))
        columns_match = columns is None or len(x_edges) == columns + 1
        rows_match = rows is None or len(y_edges) == rows + 1
        if columns_match and rows_match and len(x_edges) >= 2 and len(y_edges) >= 2:
            candidates.append((x_edges, y_edges))
            if columns is not None and rows is not None:
                return x_edges, y_edges

    if candidates:
        return max(
            candidates,
            key=lambda candidate: (
                sum(
                    len(other[0]) == len(candidate[0]) and len(other[1]) == len(candidate[1])
                    for other in candidates
                ),
                len(candidate[0]) * len(candidate[1]),
            ),
        )

    expected_columns = f"{columns + 1}" if columns is not None else "at least 2"
    expected_rows = f"{rows + 1}" if rows is not None else "at least 2"
    threshold, x_count, y_count = min(
        attempts,
        key=lambda attempt: (
            abs(attempt[1] - columns - 1) if columns is not None else 0
        )
        + (abs(attempt[2] - rows - 1) if rows is not None else 0),
    )
    raise ValueError(
        "Could not detect the expected grid: "
        f"closest result at threshold {threshold} found {x_count} vertical and "
        f"{y_count} horizontal lines; expected {expected_columns} and {expected_rows}. "
        "Adjust --grid-threshold/--grid-coverage, or use --equal-grid."
    )


def normalized_symbol_crop(
    cell: Image.Image,
    threshold: int,
    output_size: int,
    content_ratio: float,
    top_trim_ratio: float,
    cell_margin_ratio: float,
) -> Image.Image:
    width, height = cell.size
    left = round(width * cell_margin_ratio)
    right = width - left
    top = round(height * top_trim_ratio)
    bottom = height - round(height * cell_margin_ratio)
    search_area = cell.crop((left, top, right, bottom)).convert("RGB")

    grayscale = np.asarray(search_area.convert("L"))
    dark_y, dark_x = np.where(grayscale < threshold)
    if not len(dark_x):
        raise ValueError("No dark symbol pixels found.")

    symbol_left = int(dark_x.min())
    symbol_right = int(dark_x.max()) + 1
    symbol_top = int(dark_y.min())
    symbol_bottom = int(dark_y.max()) + 1
    symbol = search_area.crop((symbol_left, symbol_top, symbol_right, symbol_bottom))
    if symbol.width < 3 or symbol.height < 3:
        raise ValueError(f"Detected content is too small: {symbol.width}x{symbol.height}.")

    target = round(output_size * content_ratio)
    scale = min(target / symbol.width, target / symbol.height)
    resized = symbol.resize(
        (max(1, round(symbol.width * scale)), max(1, round(symbol.height * scale))),
        Image.Resampling.LANCZOS,
    )
    output = Image.new("RGB", (output_size, output_size), "white")
    output.paste(
        resized,
        ((output_size - resized.width) // 2, (output_size - resized.height) // 2),
    )
    return output


def output_paths(output_dir: Path, count: int) -> list[Path]:
    return [output_dir / f"{index:03d}.png" for index in range(1, count + 1)]


def crop_sheet(
    source_path: Path,
    output_dir: Path,
    rows: int | None,
    columns: int | None,
    threshold: int,
    output_size: int,
    content_ratio: float,
    top_trim_ratio: float,
    cell_margin_ratio: float,
    grid_threshold: int,
    grid_coverage: float,
    equal_grid: bool,
    overwrite: bool,
) -> None:
    if source_path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image extension: {source_path.suffix}")
    if not source_path.is_file():
        raise FileNotFoundError(f"Source image not found: {source_path}")

    with Image.open(source_path) as opened:
        sheet = opened.convert("RGB")

    if equal_grid:
        if rows is None or columns is None:
            raise ValueError("--equal-grid requires explicit --rows and --columns.")
        x_edges = [round(index * sheet.width / columns) for index in range(columns + 1)]
        y_edges = [round(index * sheet.height / rows) for index in range(rows + 1)]
    else:
        x_edges, y_edges = detect_grid_edges(
            sheet,
            rows,
            columns,
            grid_threshold,
            grid_coverage,
        )
        columns = len(x_edges) - 1
        rows = len(y_edges) - 1

    paths = output_paths(output_dir, rows * columns)
    collisions = [path for path in paths if path.exists()]
    if collisions and not overwrite:
        raise FileExistsError(
            f"{len(collisions)} output files already exist in {output_dir}. "
            "Use --overwrite only if replacing them is intentional."
        )
    crops = []
    failures = []
    for row in range(rows):
        for column in range(columns):
            index = row * columns + column + 1
            cell = sheet.crop(
                (
                    x_edges[column],
                    y_edges[row],
                    x_edges[column + 1],
                    y_edges[row + 1],
                )
            )
            try:
                crops.append(
                    normalized_symbol_crop(
                        cell,
                        threshold,
                        output_size,
                        content_ratio,
                        top_trim_ratio,
                        cell_margin_ratio,
                    )
                )
            except ValueError as exc:
                failures.append(f"{index:03d}: {exc}")

    if failures:
        details = "\n".join(failures)
        raise ValueError(f"Could not crop every grid cell:\n{details}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_dir.parent,
        prefix=f".{output_dir.name}.crop-tmp-",
    ) as temporary_name:
        temporary_dir = Path(temporary_name)
        for path, crop in zip(output_paths(temporary_dir, len(crops)), crops):
            crop.save(path)
        output_dir.mkdir(parents=True, exist_ok=True)
        for temporary_path, final_path in zip(
            output_paths(temporary_dir, len(crops)),
            paths,
        ):
            temporary_path.replace(final_path)

    print(f"Saved {len(crops)} crops to: {output_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Crop a regularly spaced symbol sheet into numbered classifier images."
    )
    parser.add_argument("source", type=Path, help="Input symbol-sheet image.")
    parser.add_argument(
        "category",
        type=category_name,
        help="Output category folder, for example FLATNESS or POSITION.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root containing category folders. Defaults to this script's directory.",
    )
    parser.add_argument(
        "--rows",
        type=int,
        help="Expected row count. Defaults to automatic grid detection.",
    )
    parser.add_argument(
        "--columns",
        type=int,
        help="Expected column count. Defaults to automatic grid detection.",
    )
    parser.add_argument("--threshold", type=int, default=160)
    parser.add_argument("--size", type=int, default=128, help="Output image width and height.")
    parser.add_argument(
        "--content-ratio",
        type=float,
        default=0.7,
        help="Maximum fraction of the output occupied by the symbol.",
    )
    parser.add_argument(
        "--top-trim-ratio",
        type=float,
        default=0.28,
        help="Fraction removed from each cell's top to exclude numbering.",
    )
    parser.add_argument(
        "--no-numbering",
        action="store_true",
        help="Use a small top margin because the sheet has no cell numbers.",
    )
    parser.add_argument(
        "--cell-margin-ratio",
        type=float,
        default=0.05,
        help="Fraction removed from cell sides and bottom to exclude grid lines.",
    )
    parser.add_argument(
        "--grid-threshold",
        type=int,
        default=245,
        help="Starting threshold for adaptive light-grid detection.",
    )
    parser.add_argument(
        "--grid-coverage",
        type=float,
        default=0.8,
        help="Minimum image coverage required to identify a grid line.",
    )
    parser.add_argument(
        "--equal-grid",
        action="store_true",
        help="Split the full image equally instead of detecting visible grid lines.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing numbered PNG files in the category folder.",
    )
    args = parser.parse_args()

    if args.rows is not None and args.rows < 1:
        parser.error("--rows must be positive.")
    if args.columns is not None and args.columns < 1:
        parser.error("--columns must be positive.")
    if not 0 <= args.threshold <= 255:
        parser.error("--threshold must be between 0 and 255.")
    if not 0 <= args.grid_threshold <= 255:
        parser.error("--grid-threshold must be between 0 and 255.")
    if args.size < 16:
        parser.error("--size must be at least 16.")
    if not 0 < args.content_ratio <= 1:
        parser.error("--content-ratio must be greater than 0 and at most 1.")
    if not 0 <= args.top_trim_ratio < 0.8:
        parser.error("--top-trim-ratio must be at least 0 and less than 0.8.")
    if not 0 <= args.cell_margin_ratio < 0.4:
        parser.error("--cell-margin-ratio must be at least 0 and less than 0.4.")
    if not 0 < args.grid_coverage <= 1:
        parser.error("--grid-coverage must be greater than 0 and at most 1.")

    output_dir = args.output_root.resolve() / args.category
    if not output_dir.is_dir():
        raise FileNotFoundError(
            f"Category folder does not exist: {output_dir}. "
            "Create it intentionally before cropping."
        )
    top_trim_ratio = 0.05 if args.no_numbering else args.top_trim_ratio
    crop_sheet(
        args.source.resolve(),
        output_dir,
        args.rows,
        args.columns,
        args.threshold,
        args.size,
        args.content_ratio,
        top_trim_ratio,
        args.cell_margin_ratio,
        args.grid_threshold,
        args.grid_coverage,
        args.equal_grid,
        args.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
