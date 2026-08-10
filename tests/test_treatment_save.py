import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


class _ConnectionWrapper:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


def _setup_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE treatments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT,
            field_id INTEGER,
            treatment_date TEXT,
            treatment_type TEXT,
            product TEXT,
            product_category TEXT,
            product_name TEXT,
            product_unit TEXT,
            product_price REAL,
            dose TEXT,
            area_ha REAL,
            crop_id INTEGER,
            crop_name TEXT,
            notes TEXT,
            products_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT,
            treatment_id INTEGER,
            cost_type TEXT,
            amount_pln REAL,
            supplier TEXT,
            invoice_no TEXT,
            notes TEXT
        )
        """
    )


def test_save_treatments_inserts_rows_and_returns_count(monkeypatch):
    conn = sqlite3.connect(":memory:")
    _setup_schema(conn)

    monkeypatch.setattr(app, "get_connection", lambda: _ConnectionWrapper(conn))
    monkeypatch.setattr(app, "_current_owner", lambda: "tester")
    monkeypatch.setattr(app, "get_field_plot_area", lambda _field_id: 10.0)
    monkeypatch.setattr(app, "resolve_treatment_crop", lambda *_args, **_kwargs: (None, ""))

    products = [
        {
            "category": "ŚOR",
            "product_name": "Herbicyd A",
            "price_per_unit": 50.0,
            "unit": "l",
            "dose": 1.0,
            "area_ha": 10.0,
        },
        {
            "category": "Maszyny",
            "product_name": "Oprysk",
            "price_per_unit": 120.0,
            "unit": "ha",
            "dose": 1.0,
            "area_ha": 10.0,
        },
    ]

    inserted_count = app.save_treatments(
        field_ids=[1, 2],
        treatment_date="2026-08-10",
        treatment_type="2026",
        products=products,
        notes="test",
    )

    assert inserted_count == 2

    treatment_count = conn.execute("SELECT COUNT(*) FROM treatments").fetchone()[0]
    costs_count = conn.execute("SELECT COUNT(*) FROM costs").fetchone()[0]

    assert treatment_count == 2
    assert costs_count == 4


def test_save_treatments_returns_zero_for_invalid_payload(monkeypatch):
    conn = sqlite3.connect(":memory:")
    _setup_schema(conn)

    monkeypatch.setattr(app, "get_connection", lambda: _ConnectionWrapper(conn))
    monkeypatch.setattr(app, "_current_owner", lambda: "tester")

    assert app.save_treatments([], "2026-08-10", "2026", [], "") == 0
