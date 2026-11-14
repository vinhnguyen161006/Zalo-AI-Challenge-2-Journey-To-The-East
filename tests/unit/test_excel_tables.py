from openpyxl import Workbook

from datamate.data.excel_tables import table_regions


def test_group_header_row_above_column_names_is_skipped() -> None:
    sheet = Workbook().active
    sheet.append(["Đơn", None, "Tiền", None])
    sheet.append(["Khách", "Mã đơn", "Giá", "Thành tiền"])
    sheet.append(["lẻ", "ORD1", 45000, "90k"])
    [(header_row, first_column, rows)] = table_regions(sheet)
    assert header_row == 2
    assert first_column == 1
    assert rows[0] == ["Khách", "Mã đơn", "Giá", "Thành tiền"]
    assert rows[1] == ["lẻ", "ORD1", 45000, "90k"]


def test_full_header_followed_by_text_row_is_kept() -> None:
    sheet = Workbook().active
    sheet.append(["Khách", "Mã đơn"])
    sheet.append(["lẻ", "ORD1"])
    [(header_row, _, rows)] = table_regions(sheet)
    assert header_row == 1
    assert rows[0] == ["Khách", "Mã đơn"]


def test_header_missing_one_name_is_not_a_group_header() -> None:
    sheet = Workbook().active
    sheet.append(["Khách", "Mã đơn", None])
    sheet.append(["lẻ", "ORD1", "ghi chú"])
    sheet.append(["An", "ORD2", "x"])
    [(header_row, _, rows)] = table_regions(sheet)
    assert header_row == 1


def test_report_title_block_is_not_a_table() -> None:
    sheet = Workbook().active
    sheet.append(["SỔ CHI TIẾT BÁN HÀNG"])
    sheet.append(["Từ ngày 01/01/2025 đến ngày 31/01/2025"])
    sheet.append([])
    sheet.append(["Mã đơn", "Tổng tiền"])
    sheet.append(["ORD1", 25000])
    [(header_row, _, rows)] = table_regions(sheet)
    assert header_row == 4
    assert rows[0] == ["Mã đơn", "Tổng tiền"]


def test_two_cell_filter_caption_is_not_a_table() -> None:
    sheet = Workbook().active
    sheet.append(["Trạng thái đơn hàng", "Đặt hàng (Đã duyệt)"])
    sheet.append(["Từ ngày 01/01/2025 đến ngày 31/01/2025"])
    sheet.append([])
    sheet.append(["Mã đơn", "Ngày", "Tổng tiền"])
    sheet.append(["ORD1", "2026-01-05", 25000])
    [(header_row, _, rows)] = table_regions(sheet)
    assert header_row == 4
