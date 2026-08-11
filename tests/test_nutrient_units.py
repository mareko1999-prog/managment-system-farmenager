import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


def test_fertilizer_summary_converts_tonnes_to_kg(monkeypatch):
    report_df = pd.DataFrame([
        {
            "product_category": "Nawozy",
            "product_name": "Nawoz T",
            "dose": 1.0,
            "area_ha": 1.0,
        },
        {
            "product_category": "Nawozy",
            "product_name": "Nawoz KG",
            "dose": 1.0,
            "area_ha": 1.0,
        },
    ])

    nawozy_catalog = pd.DataFrame([
        {"name": "Nawoz T", "unit": "t", "n_pct": 10.0, "p2o5_pct": 0.0, "k2o_pct": 0.0, "so3_pct": 0.0, "cao_pct": 0.0},
        {"name": "Nawoz KG", "unit": "kg", "n_pct": 10.0, "p2o5_pct": 0.0, "k2o_pct": 0.0, "so3_pct": 0.0, "cao_pct": 0.0},
    ])

    monkeypatch.setattr(app, "_current_owner", lambda: "tester")
    monkeypatch.setattr(app, "load_product_catalog", lambda _cat, _owner: nawozy_catalog)

    summary_df = app.build_fertilizer_nutrient_summary(report_df, reference_area_ha=1.0)

    assert not summary_df.empty
    # 1 t => 1000 kg, with 10% N gives 100 kg N + 0.1 kg from 1 kg product
    assert summary_df.iloc[0]["N [kg/ha]"] == 100.1
