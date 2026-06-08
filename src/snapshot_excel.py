import argparse
import gc
import sys
import time
from pathlib import Path

from PIL import ImageGrab


DEFAULT_SHEET = "MIP Results"
XL_SCREEN = 1
XL_BITMAP = 2
XL_PICTURE = -4147


def snapshot_workbook(
    workbook_path,
    output_path=None,
    sheet_name=DEFAULT_SHEET,
    cell_range=None,
    min_rows=26,
    min_cols=19,
    visible=False,
):
    workbook_path = Path(workbook_path).resolve()
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    pythoncom, win32com = _require_excel_com()
    output_path = Path(output_path).resolve() if output_path else _default_output_path(workbook_path, sheet_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    excel = None
    workbook = None
    worksheet = None
    target = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = bool(visible)
        excel.DisplayAlerts = False
        excel.ScreenUpdating = True

        workbook = excel.Workbooks.Open(str(workbook_path), UpdateLinks=0, ReadOnly=True)
        worksheet = workbook.Worksheets(sheet_name)
        target = worksheet.Range(cell_range) if cell_range else _auto_range(worksheet, min_rows, min_cols)

        worksheet.Activate()
        target.Select()
        _copy_range_to_png(target, output_path)
        address = str(target.Address)
        target = None
        worksheet = None
        return {
            "workbook": str(workbook_path),
            "sheet": sheet_name,
            "range": address.replace("$", ""),
            "output": str(output_path),
        }
    finally:
        target = None
        worksheet = None
        if workbook is not None:
            workbook.Close(SaveChanges=False)
            workbook = None
        if excel is not None:
            excel.CutCopyMode = False
            excel.Quit()
            excel = None
        gc.collect()
        pythoncom.CoUninitialize()


def _auto_range(worksheet, min_rows, min_cols):
    used = worksheet.UsedRange
    last_row = max(used.Row + used.Rows.Count - 1, min_rows)
    last_col = max(used.Column + used.Columns.Count - 1, min_cols)
    return worksheet.Range(worksheet.Cells(1, 1), worksheet.Cells(last_row, last_col))


def _copy_range_to_png(target, output_path):
    target.CopyPicture(Appearance=XL_SCREEN, Format=XL_BITMAP)

    image = None
    for _ in range(20):
        image = ImageGrab.grabclipboard()
        if hasattr(image, "save"):
            image.save(output_path, "PNG")
            return
        time.sleep(0.1)

    _export_range_via_temp_chart(target, output_path)


def _export_range_via_temp_chart(target, output_path):
    worksheet = target.Worksheet
    target.CopyPicture(Appearance=XL_SCREEN, Format=XL_PICTURE)
    chart_object = worksheet.ChartObjects().Add(target.Left, target.Top, target.Width, target.Height)
    try:
        chart_object.Activate()
        chart = chart_object.Chart
        chart.Paste()
        chart.Export(str(output_path), "PNG")
    finally:
        chart_object.Delete()


def _default_output_path(workbook_path, sheet_name):
    safe_sheet = "".join(ch if ch.isalnum() else "_" for ch in sheet_name).strip("_")
    return workbook_path.with_name(f"{workbook_path.stem}_{safe_sheet}_snapshot.png")


def _require_excel_com():
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "pywin32 is required for Excel snapshots. Install it with: "
            "uv pip install --python .\\.venv\\Scripts\\python.exe pywin32"
        ) from exc
    return pythoncom, win32com


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Create a PNG snapshot from an Excel worksheet range.")
    parser.add_argument("workbook", help="Path to .xls or .xlsx workbook.")
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help=f"Worksheet name. Default: {DEFAULT_SHEET!r}.")
    parser.add_argument("--range", dest="cell_range", help="Excel range to capture, e.g. A1:S30.")
    parser.add_argument("--output", help="Output PNG path. Defaults beside the workbook.")
    parser.add_argument("--min-rows", type=int, default=26, help="Minimum last row for auto range.")
    parser.add_argument("--min-cols", type=int, default=19, help="Minimum last column for auto range.")
    parser.add_argument("--visible", action="store_true", help="Show Excel while taking the snapshot.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv or sys.argv[1:])
    try:
        result = snapshot_workbook(
            args.workbook,
            output_path=args.output,
            sheet_name=args.sheet,
            cell_range=args.cell_range,
            min_rows=args.min_rows,
            min_cols=args.min_cols,
            visible=args.visible,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"snapshot: {result['output']}")
    print(f"sheet: {result['sheet']}")
    print(f"range: {result['range']}")


if __name__ == "__main__":
    main()
