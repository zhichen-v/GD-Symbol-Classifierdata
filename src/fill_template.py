import argparse
import json
import os
import sys

import xlrd
import xlwt
from xlutils.copy import copy as copy_workbook


RESULT_SHEET = "MIP Results"
DATA_START_ROW = 13
DEFAULT_DATA_ROWS = 13
DATA_END_ROW = DATA_START_ROW + DEFAULT_DATA_ROWS - 1
STATIC_END_ROW = DATA_START_ROW

COL_ITEM = 0
COL_DRAWING_SHEET = 1
COL_ZONE = 2
COL_SPECIFICATION = 3
COL_TOLERANCE = 4
COL_SUQC = 5
COL_IPQC = 6
COL_OGQC = 7
COL_EQUIPMENT = 8
COL_PRODUCTION = 9


def fill_template(template_path, output_rows, output_path):
    rb = xlrd.open_workbook(template_path, formatting_info=True)
    sheet_index = rb.sheet_names().index(RESULT_SHEET)
    rs = rb.sheet_by_index(sheet_index)
    wb = copy_workbook(rb)
    ws = wb.get_sheet(sheet_index)
    style_cache = {}
    static_style_cache = {}
    data_cols = range(rs.ncols)

    visible_count = len(output_rows)
    write_count = max(visible_count, DEFAULT_DATA_ROWS)

    for offset in range(write_count):
        rowx = DATA_START_ROW + offset
        if offset < visible_count:
            _show_data_row(ws, rs, rowx, offset, visible_count)
            _write_blank_data_row(rb, ws, rs, style_cache, rowx, offset, visible_count, data_cols)
        else:
            _clear_and_hide_row(rb, ws, rs, style_cache, rowx, offset, data_cols)

    for index, row in enumerate(output_rows, start=1):
        offset = index - 1
        rowx = DATA_START_ROW + index - 1
        _write_data(rb, ws, rs, style_cache, rowx, COL_ITEM, index, offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_DRAWING_SHEET, row.get("drawing_sheet", ""), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_ZONE, row.get("zone", ""), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_SPECIFICATION, row.get("excel_specification", ""), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_TOLERANCE, row.get("excel_tolerance", ""), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_SUQC, row.get("suqc", "*"), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_IPQC, row.get("ipqc", "\u25cb"), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_OGQC, row.get("ogqc", "\u25ce"), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_EQUIPMENT, row.get("measuring_equipment", ""), offset, visible_count)
        _write_data(rb, ws, rs, style_cache, rowx, COL_PRODUCTION, row.get("production_section", "MCM"), offset, visible_count)

    _restore_static_region(rb, ws, rs, static_style_cache)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    wb.save(output_path)
    return output_path


def _write_blank_data_row(rb, ws, rs, style_cache, rowx, offset, total_rows, data_cols):
    for colx in data_cols:
        value = _default_control_value(colx)
        _write_data(rb, ws, rs, style_cache, rowx, colx, value, offset, total_rows)


def _clear_and_hide_row(rb, ws, rs, style_cache, rowx, offset, data_cols):
    for colx in data_cols:
        _write_data(rb, ws, rs, style_cache, rowx, colx, "", offset, DEFAULT_DATA_ROWS)
    row = ws.row(rowx)
    row.hidden = True
    row.height = 0
    row.height_mismatch = True


def _show_data_row(ws, rs, rowx, offset, total_rows):
    source_rowx = _source_data_row(offset, total_rows)
    row = ws.row(rowx)
    row.hidden = False
    source_info = rs.rowinfo_map.get(source_rowx)
    if source_info:
        row.height = source_info.height
        row.height_mismatch = True


def _write_data(rb, ws, rs, style_cache, rowx, colx, value, offset, total_rows):
    style = _style_for_data_cell(rb, rs, style_cache, offset, total_rows, colx)
    ws.write(rowx, colx, value, style)


def _restore_static_region(rb, ws, rs, style_cache):
    for rowx in range(STATIC_END_ROW):
        for colx in range(rs.ncols):
            style = _style_for_static_cell(rb, rs, style_cache, rowx, colx)
            ws.write(rowx, colx, rs.cell_value(rowx, colx), style)


def _style_for_static_cell(rb, rs, style_cache, rowx, colx):
    xf_index = rs.cell_xf_index(rowx, colx)
    if xf_index not in style_cache:
        style_cache[xf_index] = _clone_style(rb, xf_index)
    return style_cache[xf_index]


def _style_for_data_cell(rb, rs, style_cache, offset, total_rows, colx):
    source_rowx = _source_data_row(offset, total_rows)
    xf_index = rs.cell_xf_index(source_rowx, colx)
    bottom_xf_index = None
    if total_rows <= 1:
        bottom_xf_index = rs.cell_xf_index(DATA_END_ROW, colx)
    cache_key = (xf_index, bottom_xf_index)
    if cache_key not in style_cache:
        style_cache[cache_key] = _clone_style_with_arial(rb, xf_index, bottom_xf_index)
    return style_cache[cache_key]


def _source_data_row(offset, total_rows):
    if total_rows <= 1 or offset == 0:
        return DATA_START_ROW
    if offset == total_rows - 1:
        return DATA_END_ROW
    middle_rows = DEFAULT_DATA_ROWS - 2
    return DATA_START_ROW + 1 + ((offset - 1) % middle_rows)


def _default_control_value(colx):
    if colx == COL_SUQC:
        return "*"
    if colx == COL_IPQC:
        return "\u25cb"
    if colx == COL_OGQC:
        return "\u25ce"
    return ""


def _clone_style_with_arial(rb, xf_index, bottom_xf_index=None):
    style = _clone_style(rb, xf_index)
    style.font.name = "Arial"
    if bottom_xf_index is not None:
        bottom_xf = rb.xf_list[bottom_xf_index]
        style.borders.bottom = bottom_xf.border.bottom_line_style
        style.borders.bottom_colour = bottom_xf.border.bottom_colour_index
    return style


def _clone_style(rb, xf_index):
    xf = rb.xf_list[xf_index]
    font = rb.font_list[xf.font_index]
    style = xlwt.XFStyle()

    style.num_format_str = rb.format_map[xf.format_key].format_str

    style.font.name = "Arial"
    style.font.height = font.height
    style.font.italic = bool(font.italic)
    style.font.struck_out = bool(font.struck_out)
    style.font.outline = bool(font.outline)
    style.font.shadow = bool(font.shadow)
    style.font.colour_index = font.colour_index
    style.font.bold = bool(font.bold)
    style.font._weight = font.weight
    style.font.escapement = font.escapement
    style.font.underline = font.underline_type
    style.font.family = font.family
    style.font.charset = font.character_set

    style.alignment.horz = xf.alignment.hor_align
    style.alignment.vert = xf.alignment.vert_align
    style.alignment.dire = xf.alignment.text_direction
    style.alignment.rota = xf.alignment.rotation
    style.alignment.wrap = xf.alignment.text_wrapped
    style.alignment.shri = xf.alignment.shrink_to_fit
    style.alignment.inde = xf.alignment.indent_level

    style.borders.left = xf.border.left_line_style
    style.borders.right = xf.border.right_line_style
    style.borders.top = xf.border.top_line_style
    style.borders.bottom = xf.border.bottom_line_style
    style.borders.diag = xf.border.diag_line_style
    style.borders.left_colour = xf.border.left_colour_index
    style.borders.right_colour = xf.border.right_colour_index
    style.borders.top_colour = xf.border.top_colour_index
    style.borders.bottom_colour = xf.border.bottom_colour_index
    style.borders.diag_colour = xf.border.diag_colour_index
    style.borders.need_diag1 = xf.border.diag_up
    style.borders.need_diag2 = xf.border.diag_down

    style.pattern.pattern = xf.background.fill_pattern
    style.pattern.pattern_fore_colour = xf.background.pattern_colour_index
    style.pattern.pattern_back_colour = xf.background.background_colour_index

    style.protection.cell_locked = xf.protection.cell_locked
    style.protection.formula_hidden = xf.protection.formula_hidden
    return style


def _load_output_rows(debug_path):
    with open(debug_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data["output_rows"]


def _configure_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Fill template.xls from normalized debug JSON.")
    parser.add_argument("--template", default="template.xls")
    parser.add_argument("--debug", default="output/extraction_debug.json")
    parser.add_argument("--output", default="output/MIP_filled.xls")
    args = parser.parse_args(argv)

    path = fill_template(args.template, _load_output_rows(args.debug), args.output)
    print(json.dumps({"status": "success", "output": path}, ensure_ascii=False))


if __name__ == "__main__":
    main()
