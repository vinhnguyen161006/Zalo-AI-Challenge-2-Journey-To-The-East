from datamate.semantics.question import language_hints
from datamate.semantics.source_notes import column_notes, order_key_names


def test_language_hints_do_not_confuse_drug_strength_or_requested_multiple_columns() -> None:
    hint = language_hints("ngay nao trong thang 4/2026 dthu cao nhat")
    assert hint["expected_answer_type"] == "date" and hint["group_entity"] == "calendar_day"
    assert "doanh thu" in hint["expanded_question"]
    assert (
        language_hints("tỉ lệ đơn huỷ tháng này là bao nhiêu %")["expected_answer_type"]
        == "percent"
    )
    assert "expected_answer_type" not in language_hints("Bán bao nhiêu chai Glucose 5%?")
    assert "expected_answer_type" not in language_hints("Ngày nào doanh thu cao nhất và số tiền?")
    assert "expected_answer_type" not in language_hints("Thứ nào trong tuần bán nhiều nhất?")


def test_column_meanings_are_scoped_to_the_documented_source_file() -> None:
    readme = """## Cột trong `sales.csv`
- **Mã bill**: mã đơn
- **Đã trả**: tiền khách thanh toán
## Cột trong `returns.csv`
- **Đã trả**: số lượng hàng trả
## Quy ước
- **Đã trả**: ghi chú khác, không phải định nghĩa cột
"""
    sales = column_notes(readme, "sales.csv")
    assert sales["da_tra"]["description"] == "tiền khách thanh toán"
    assert column_notes(readme, "returns.csv")["da_tra"]["description"] == "số lượng hàng trả"
    assert column_notes(readme, "other.csv") == {}
    assert order_key_names(readme, "sales.csv") == {"ma_bill"}
