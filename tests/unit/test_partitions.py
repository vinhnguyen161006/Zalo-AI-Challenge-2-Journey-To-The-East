from datamate.data.partitions import file_family, month_label


def test_month_in_file_name_is_a_partition_label() -> None:
    assert month_label("BanHang_T10_2025") == "2025-10"
    assert month_label("BanHang_Thang01_2026") == "2026-01"


def test_monthly_files_share_one_family() -> None:
    assert file_family("BanHang_T10_2025.xlsx") == file_family("BanHang_T1_2026.xlsx")
    assert file_family("SoBanHang_1.xlsx") == file_family("SoBanHang_2026.xlsx")
    assert file_family("Ban.csv") != file_family("Nhap.csv")


def test_accented_month_title_is_a_partition_label() -> None:
    assert month_label("Tháng 01-2025") == "2025-01"
    assert month_label("Tháng 12/2025") == "2025-12"
