"""Create tiny, invented binary fixtures. Does not read competition data or labels."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from openpyxl import Workbook


def create(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    sheet.append(["SYNTHETIC REPORT"])
    sheet.append(["product_id", "product_name", "unit"])
    sheet.append(["p1", "Cà phê sữa", "cup"])
    sheet.append(["p2", "Cà phê đá", "cup"])
    sheet.append([])
    sheet.append(["unit", "factor"])
    sheet.append(["cup", 1])
    second = workbook.create_sheet("Aliases")
    second.append(["product_id", "alias"])
    second.append(["p1", "cafe sua"])
    second.append(["p2", "cafe da"])
    workbook.save(root / "products.xlsx")
    database = root / "customers.sqlite"
    if not database.exists():
        with sqlite3.connect(database) as con:
            con.execute("CREATE TABLE customers (customer_id TEXT PRIMARY KEY, name TEXT)")
            con.executemany(
                "INSERT INTO customers VALUES (?, ?)", [("c1", "Chị Lan"), ("c2", "Anh Nam")]
            )


if __name__ == "__main__":
    create(Path(__file__).resolve().parents[1] / "tests/fixtures/synthetic_business/data/biz_demo")
