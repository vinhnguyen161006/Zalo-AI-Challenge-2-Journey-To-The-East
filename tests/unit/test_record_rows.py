import pytest

from datamate.data.ingest import record_rows

HEADER = ["ngay", "ma_don", "ten_hang", "thanh_tien"]


@pytest.mark.parametrize(
    "label", ["Tổng ngày 15/07", "Cộng ngày 1/1/2025", "Tổng tháng 5/2026", "Cộng", "Tổng cộng"]
)
def test_embedded_subtotal_rows_are_not_records(label: str) -> None:
    body = [["2025-07-15", "o1", "Bia", 30000], ["2025-07-15", None, label, 30000]]
    records, sources, notes = record_rows(body, [2, 3], HEADER)
    assert records == [["2025-07-15", "o1", "Bia", 30000]]
    assert sources == [2]
    assert notes == ["excluded_subtotal_row:row=3"]


def test_repeated_header_row_is_not_a_record() -> None:
    body = [["2025-07-15", "o1", "Bia", 30000], ["Ngay", "Ma_don", "Ten_hang", "Thanh_tien"]]
    records, _, notes = record_rows(body, [2, 3], HEADER)
    assert len(records) == 1
    assert notes == ["excluded_repeated_header:row=3"]


def test_item_named_like_a_total_word_is_kept() -> None:
    body = [["2025-07-15", "o1", "Tổng hợp rau củ", 30000]]
    records, _, _ = record_rows(body, [2], HEADER)
    assert len(records) == 1


def test_many_row_notes_collapse_to_one_count() -> None:
    body = [["2025-07-15", "o1", "Bia", 1]] + [["2025-07-15", None, "Cộng", 1]] * 30
    _, _, notes = record_rows(body, list(range(31)), HEADER)
    assert notes == ["excluded_subtotal_row:rows=30"]
