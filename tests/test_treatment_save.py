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

    def rollback(self):
        self._conn.rollback()


def _setup_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE treatments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT,
            batch_id TEXT,
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
    batch_ids = [row[0] for row in conn.execute("SELECT batch_id FROM treatments").fetchall()]

    assert treatment_count == 2
    assert costs_count == 4
    assert len(set(batch_ids)) == 1
    assert batch_ids[0]


def test_save_treatments_returns_zero_for_invalid_payload(monkeypatch):
    conn = sqlite3.connect(":memory:")
    _setup_schema(conn)

    monkeypatch.setattr(app, "get_connection", lambda: _ConnectionWrapper(conn))
    monkeypatch.setattr(app, "_current_owner", lambda: "tester")

    assert app.save_treatments([], "2026-08-10", "2026", [], "") == 0


def test_treatment_list_groups_all_fields_from_one_batch(monkeypatch):
    treatments_df = app.pd.DataFrame([
        {
            "id": 11,
            "batch_id": "batch-1",
            "field_id": 1,
            "field_name": "Pole A",
            "treatment_date": "2026-08-10",
            "season": "2026",
            "product_name": "Herbicyd A",
            "product": "Herbicyd A",
            "notes": "",
            "products_json": '[{"product_name": "Herbicyd A", "dose": 1, "unit": "l"}]',
        },
        {
            "id": 12,
            "batch_id": "batch-1",
            "field_id": 2,
            "field_name": "Pole B",
            "treatment_date": "2026-08-10",
            "season": "2026",
            "product_name": "Herbicyd A",
            "product": "Herbicyd A",
            "notes": "",
            "products_json": '[{"product_name": "Herbicyd A", "dose": 1, "unit": "l"}]',
        },
    ])

    monkeypatch.setattr(app, "get_field_plot_area", lambda field_id: {1: 4.0, 2: 6.0}[field_id])

    groups = app.build_treatment_list_groups(treatments_df)

    assert len(groups) == 1
    assert groups[0]["id"] == "batch-1"
    assert groups[0]["field_name"] == "Pole A, Pole B"
    assert groups[0]["total_area_ha"] == 10.0


def test_treatment_registry_groups_same_rows_by_date_crop_product_and_dose():
    farms_df = app.pd.DataFrame([{"id": 7, "name": "Gospodarstwo A"}])
    plots_df = app.pd.DataFrame([
        {"farm_name": "Gospodarstwo A", "field_id": 10, "name": "10", "area_ha": 1.5},
        {"farm_name": "Gospodarstwo A", "field_id": 11, "name": "11", "area_ha": 2.5},
    ])
    treatments_df = app.pd.DataFrame([
        {
            "field_id": 10,
            "season": "2026",
            "treatment_date": "2026-08-10",
            "crop_name": "Pszenica",
            "product_category": "ŚOR",
            "product_name": "Herbicyd A",
            "dose": 1.0,
            "notes": "",
            "products_json": '[{"product_name": "Herbicyd A", "dose": 1.0, "category": "ŚOR"}]',
        },
        {
            "field_id": 11,
            "season": "2026",
            "treatment_date": "2026-08-10",
            "crop_name": "Pszenica",
            "product_category": "ŚOR",
            "product_name": "Herbicyd A",
            "dose": 1.0,
            "notes": "",
            "products_json": '[{"product_name": "Herbicyd A", "dose": 1.0, "category": "ŚOR"}]',
        },
    ])

    report_df = app.build_treatment_registry_report(
        7,
        "2026",
        farms_df,
        plots_df,
        app.pd.DataFrame(),
        treatments_df,
        group_fields=True,
    )

    assert len(report_df) == 1
    assert report_df.iloc[0]["plot_name"] == "10, 11"
    assert report_df.iloc[0]["area_ha"] == 4.0
    assert report_df.iloc[0]["product_name"] == "Herbicyd A"
    assert report_df.iloc[0]["dose"] == 1.0

    ungrouped_df = app.build_treatment_registry_report(
        7,
        "2026",
        farms_df,
        plots_df,
        app.pd.DataFrame(),
        treatments_df,
    )
    assert len(ungrouped_df) == 2


def test_treatment_registry_scales_area_when_treatment_area_differs_from_field_area():
    farms_df = app.pd.DataFrame([{"id": 7, "name": "Gospodarstwo A"}])
    plots_df = app.pd.DataFrame([
        {"farm_name": "Gospodarstwo A", "field_id": 10, "name": "10", "area_ha": 3.0},
        {"farm_name": "Gospodarstwo A", "field_id": 10, "name": "11", "area_ha": 7.0},
    ])
    treatments_df = app.pd.DataFrame([
        {
            "field_id": 10,
            "season": "2026",
            "treatment_date": "2026-08-10",
            "crop_name": "Pszenica",
            "product_category": "ŚOR",
            "product_name": "Herbicyd A",
            "dose": 1.0,
            "area_ha": 6.0,
            "notes": "",
            "products_json": '[{"product_name": "Herbicyd A", "dose": 1.0, "category": "ŚOR"}]',
        }
    ])

    report_df = app.build_treatment_registry_report(
        7,
        "2026",
        farms_df,
        plots_df,
        app.pd.DataFrame(),
        treatments_df,
        group_fields=True,
    )

    assert len(report_df) == 1
    assert report_df.iloc[0]["plot_name"] == "10, 11"
    assert abs(report_df.iloc[0]["area_ha"] - 6.0) < 1e-9

    treatment_row = app.pd.DataFrame([
        {
            "field_id": 10,
            "season": "2026",
            "treatment_date": "2026-08-10",
            "crop_name": "Pszenica",
            "product_category": "ŚOR",
            "product_name": "Herbicyd A",
            "dose": 1.0,
            "area_ha": 6.0,
            "notes": "",
            "products_json": '[{"product_name": "Herbicyd A", "dose": 1.0, "category": "ŚOR"}]',
        }
    ])
    report_rows = app.build_treatment_registry_report(
        7,
        "2026",
        farms_df,
        plots_df,
        app.pd.DataFrame(),
        treatment_row,
        group_fields=False,
    )
    assert len(report_rows) == 2
    assert abs(report_rows.iloc[0]["area_ha"] - 3.0 * (6.0 / 10.0)) < 1e-9
    assert abs(report_rows.iloc[1]["area_ha"] - 7.0 * (6.0 / 10.0)) < 1e-9


def test_replace_treatment_batch_rolls_back_when_replacement_fails(monkeypatch):
    conn = sqlite3.connect(":memory:")
    _setup_schema(conn)
    conn.execute(
        "INSERT INTO treatments (owner_username, batch_id, field_id, treatment_date, treatment_type) VALUES (?, ?, ?, ?, ?)",
        ("tester", "batch-1", 1, "2026-08-10", "2026"),
    )
    conn.commit()

    monkeypatch.setattr(app, "get_connection", lambda: _ConnectionWrapper(conn))
    monkeypatch.setattr(app, "_current_owner", lambda: "tester")
    monkeypatch.setattr(app, "get_field_plot_area", lambda _field_id: 10.0)
    monkeypatch.setattr(app, "resolve_treatment_crop", lambda *_args, **_kwargs: (None, ""))

    try:
        app.replace_treatment_batch(
            batch_id="batch-1",
            previous_treatment_ids=[1],
            field_ids=[1, 2],
            treatment_date="2026-08-11",
            treatment_type="2026",
            products=[{"product_name": "Niepełny produkt"}],
            notes="",
        )
    except KeyError:
        pass
    else:
        raise AssertionError("Expected invalid product data to fail")

    remaining_rows = conn.execute("SELECT id FROM treatments WHERE batch_id = ?", ("batch-1",)).fetchall()
    assert remaining_rows == [(1,)]
