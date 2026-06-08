import argparse
import json
import math
import re
import sys

import xlrd


RESULT_SHEET = "MIP Results"
EXAMPLE_SHEET = "MIP Example"
DATA_START_ROW = 13
DEFAULT_DATA_ROWS = 13
DATA_END_ROW = DATA_START_ROW + DEFAULT_DATA_ROWS - 1
COL_SUQC = 5
COL_IPQC = 6
COL_OGQC = 7


def validate_output(template_path, output_path, debug_path, example_path=None):
    template = xlrd.open_workbook(template_path, formatting_info=True)
    output = xlrd.open_workbook(output_path, formatting_info=True)
    debug = _read_json(debug_path)

    errors = []
    warnings = []
    expected_rows = debug["output_rows"]
    template_sheet = template.sheet_by_name(RESULT_SHEET)
    result_sheet = output.sheet_by_name(RESULT_SHEET)

    _validate_static_region_unchanged(template, output, template_sheet, result_sheet, errors)

    for offset, expected in enumerate(expected_rows):
        rowx = DATA_START_ROW + offset
        item = result_sheet.cell_value(rowx, 0)
        drawing_sheet = result_sheet.cell_value(rowx, 1)
        zone = result_sheet.cell_value(rowx, 2)
        spec = result_sheet.cell_value(rowx, 3)
        tolerance = result_sheet.cell_value(rowx, 4)
        equipment = result_sheet.cell_value(rowx, 8)

        if int(item) != expected["item"]:
            errors.append(f"Row {rowx + 1}: item mismatch {item!r} != {expected['item']!r}")
        if not _same_value(drawing_sheet, expected["drawing_sheet"]):
            errors.append(f"Row {rowx + 1}: drawing sheet mismatch")
        if zone != expected["zone"]:
            errors.append(f"Row {rowx + 1}: zone mismatch {zone!r} != {expected['zone']!r}")
        if not spec:
            errors.append(f"Row {rowx + 1}: specification is blank")
        if not tolerance:
            errors.append(f"Row {rowx + 1}: tolerance is blank")
        if tolerance == "UNDETERMINED":
            errors.append(f"Row {rowx + 1}: tolerance must use '-' instead of UNDETERMINED")
        if not equipment:
            errors.append(f"Row {rowx + 1}: measuring equipment is blank")
        _validate_control_items(result_sheet, rowx, errors)
        _validate_row_styles(
            template,
            output,
            template_sheet,
            result_sheet,
            rowx,
            offset,
            len(expected_rows),
            errors,
        )

    _validate_unused_rows(result_sheet, len(expected_rows), errors)
    _validate_template_sheet_list(template, output, errors)
    if example_path:
        _validate_example_reference(example_path, errors, warnings)
    elif EXAMPLE_SHEET in template.sheet_names() and EXAMPLE_SHEET in output.sheet_names():
        _validate_example_unchanged(template, output, errors)
    _validate_debug_conversions(debug, errors, warnings)

    return {
        "status": "success" if not errors else "errors_found",
        "row_count": len(expected_rows),
        "errors": errors,
        "warnings": warnings,
    }


def _validate_template_sheet_list(template, output, errors):
    if template.sheet_names() != output.sheet_names():
        errors.append(
            f"Output sheet list changed: {output.sheet_names()!r} != template {template.sheet_names()!r}"
        )


def _validate_control_items(result_sheet, rowx, errors):
    expected = {
        COL_SUQC: "*",
        COL_IPQC: "\u25cb",
        COL_OGQC: "\u25ce",
    }
    for colx, value in expected.items():
        actual = result_sheet.cell_value(rowx, colx)
        if actual != value:
            errors.append(f"Row {rowx + 1}, col {colx + 1}: expected control item {value!r}, got {actual!r}")


def _validate_row_styles(template, output, template_sheet, result_sheet, rowx, offset, total_rows, errors):
    expected_height = _source_row_height(template_sheet, offset, total_rows)
    actual_height = result_sheet.rowinfo_map.get(rowx).height if rowx in result_sheet.rowinfo_map else None
    if actual_height != expected_height:
        errors.append(f"Row {rowx + 1}: height changed {actual_height!r} != {expected_height!r}")

    for colx in range(template_sheet.ncols):
        source_rowx = _source_data_row(offset, total_rows)
        template_xf = template.xf_list[template_sheet.cell_xf_index(source_rowx, colx)]
        output_xf = output.xf_list[result_sheet.cell_xf_index(rowx, colx)]
        template_border = _expected_border_tuple(template, template_sheet, offset, total_rows, colx)
        output_border = _border_tuple(output_xf)
        if output_border != template_border:
            errors.append(f"Row {rowx + 1}, col {colx + 1}: border changed")
        output_font = output.font_list[output_xf.font_index].name
        if output_font != "Arial":
            errors.append(f"Row {rowx + 1}, col {colx + 1}: font is {output_font!r}, expected Arial")


def _validate_unused_rows(result_sheet, output_row_count, errors):
    if output_row_count >= DEFAULT_DATA_ROWS:
        return
    for offset in range(output_row_count, DEFAULT_DATA_ROWS):
        rowx = DATA_START_ROW + offset
        info = result_sheet.rowinfo_map.get(rowx)
        if not info or not info.hidden:
            errors.append(f"Row {rowx + 1}: unused template data row should be hidden")
        for colx in range(result_sheet.ncols):
            value = result_sheet.cell_value(rowx, colx)
            if value not in ("", 0):
                errors.append(f"Row {rowx + 1}, col {colx + 1}: unused row was not cleared")
                break


def _validate_static_region_unchanged(template, output, template_sheet, result_sheet, errors):
    if template_sheet.ncols != result_sheet.ncols:
        errors.append(f"Result sheet column count changed: {result_sheet.ncols} != {template_sheet.ncols}")
        return

    if sorted(template_sheet.merged_cells) != sorted(result_sheet.merged_cells):
        errors.append("Result sheet merged-cell layout changed.")

    for rowx in range(DATA_START_ROW):
        for colx in range(template_sheet.ncols):
            if template_sheet.cell_value(rowx, colx) != result_sheet.cell_value(rowx, colx):
                errors.append(f"Static region changed at row {rowx + 1}, col {colx + 1}.")
                return
            template_xf = template.xf_list[template_sheet.cell_xf_index(rowx, colx)]
            output_xf = output.xf_list[result_sheet.cell_xf_index(rowx, colx)]
            if _border_tuple(template_xf) != _border_tuple(output_xf):
                errors.append(f"Static region border changed at row {rowx + 1}, col {colx + 1}.")
                return
            template_font = template.font_list[template_xf.font_index].name
            output_font = output.font_list[output_xf.font_index].name
            if template_font != output_font:
                errors.append(f"Static region font changed at row {rowx + 1}, col {colx + 1}.")
                return


def _source_data_row(offset, total_rows):
    if total_rows <= 1 or offset == 0:
        return DATA_START_ROW
    if offset == total_rows - 1:
        return DATA_END_ROW
    middle_rows = DEFAULT_DATA_ROWS - 2
    return DATA_START_ROW + 1 + ((offset - 1) % middle_rows)


def _source_row_height(template_sheet, offset, total_rows):
    source_rowx = _source_data_row(offset, total_rows)
    info = template_sheet.rowinfo_map.get(source_rowx)
    return info.height if info else None


def _expected_border_tuple(template, template_sheet, offset, total_rows, colx):
    source_rowx = _source_data_row(offset, total_rows)
    xf = template.xf_list[template_sheet.cell_xf_index(source_rowx, colx)]
    border = list(_border_tuple(xf))
    if total_rows <= 1:
        bottom_xf = template.xf_list[template_sheet.cell_xf_index(DATA_END_ROW, colx)]
        bottom_border = _border_tuple(bottom_xf)
        border[3] = bottom_border[3]
        border[7] = bottom_border[7]
    return tuple(border)


def _border_tuple(xf):
    border = xf.border
    return (
        border.left_line_style,
        border.right_line_style,
        border.top_line_style,
        border.bottom_line_style,
        border.left_colour_index,
        border.right_colour_index,
        border.top_colour_index,
        border.bottom_colour_index,
    )


def _validate_example_reference(example_path, errors, warnings):
    example = xlrd.open_workbook(example_path, formatting_info=True)
    if EXAMPLE_SHEET not in example.sheet_names():
        errors.append(f"Example workbook does not contain {EXAMPLE_SHEET!r}.")
        return
    sheet = example.sheet_by_name(EXAMPLE_SHEET)
    expected = [
        "Item #",
        "Drawing Sheet",
        "ZONE",
        "Specification\nImperial (Metric)",
        "Tolerance\nImperial (Metric)",
        "CONTROL ITEMS",
        "",
        "",
        "Measuring Equipment",
        "Production       Section",
    ]
    actual = [sheet.cell_value(11, colx) for colx in range(10)]
    if actual != expected:
        errors.append("Example workbook header row does not match expected MIP convention.")
    if sheet.nrows < 26:
        warnings.append("Example workbook has fewer rows than the current reference sample.")


def _validate_example_unchanged(template, output, errors):
    source = template.sheet_by_name(EXAMPLE_SHEET)
    produced = output.sheet_by_name(EXAMPLE_SHEET)
    if source.nrows != produced.nrows or source.ncols != produced.ncols:
        errors.append("MIP Example sheet dimensions changed.")
        return
    for rowx in range(source.nrows):
        for colx in range(source.ncols):
            if source.cell_value(rowx, colx) != produced.cell_value(rowx, colx):
                errors.append(f"MIP Example changed at row {rowx + 1}, col {colx + 1}.")
                return


def _validate_debug_conversions(debug, errors, warnings):
    for row in debug["normalized_rows"]:
        parsed = row["parsed_specification"]
        if parsed.get("unit") == "inch" and parsed.get("metric_nominal"):
            nominal = parsed.get("nominal", "")
            if not nominal:
                continue
            numbers = re.findall(r"(?:\d+\.\d+|\.\d+|\d+)", nominal)
            if not numbers:
                continue
            number = numbers[-1]
            expected = float(number) * 25.4
            actual = float(parsed["metric_nominal"])
            if not math.isclose(expected, actual, rel_tol=0, abs_tol=0.001):
                errors.append(f"Metric conversion mismatch for {nominal}: {actual} != {expected}")
        warnings.extend(row.get("warnings", []))


def _same_value(actual, expected):
    if isinstance(expected, float):
        try:
            return math.isclose(float(actual), expected, rel_tol=0, abs_tol=0.000001)
        except (TypeError, ValueError):
            return False
    return actual == expected


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Validate filled MIP output.")
    parser.add_argument("--template", default="template.xls")
    parser.add_argument("--example", default="example.xls")
    parser.add_argument("--output", default="output/MIP_filled.xls")
    parser.add_argument("--debug", default="output/extraction_debug.json")
    args = parser.parse_args(argv)

    result = validate_output(args.template, args.output, args.debug, example_path=args.example)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
