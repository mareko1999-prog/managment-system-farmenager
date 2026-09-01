import io
import importlib
import json
import math
import os
import re
import time
from urllib import parse, request
from uuid import uuid4
from datetime import date
from typing import Any, Optional

import pandas as pd
import streamlit as st
try:
    st_keyup = importlib.import_module("st_keyup").st_keyup
except ImportError:
    st_keyup = None

import db
from auth import delete_registered_user, is_admin_username, list_registered_users, set_registered_user_password, SESSION_AUTH_USERNAME, require_authentication, show_password_change_form

BASE_DIR = os.path.dirname(__file__)
SOREGISTRY_PATH = os.path.join(os.path.dirname(__file__), "rejestr_sor_20260720.xlsx")
PRODUCT_TABLES = ["ŚOR", "Nawozy", "Materiał siewny", "Maszyny"]
OWNED_TABLES = [
    "fields",
    "farms",
    "seasons",
    "plots",
    "treatments",
    "costs",
    "crops",
    "field_crop_assignments",
    *PRODUCT_TABLES,
]


def _dataframe_from_rows(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def _current_owner() -> str:
    return str(st.session_state.get(SESSION_AUTH_USERNAME) or "").strip().lower()


def _count_owner_rows(table_name: str, owner_username: str) -> int:
    with get_connection() as conn:
        try:
            row = conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}" WHERE owner_username = ?',
                (owner_username,),
            ).fetchone()
        except Exception:
            return 0
    return int(row[0]) if row else 0


def _load_owner_table_preview(table_name: str, owner_username: str, limit: int = 50) -> pd.DataFrame:
    with get_connection() as conn:
        try:
            cursor = conn.execute(
                f'SELECT * FROM "{table_name}" WHERE owner_username = ? LIMIT ?',
                (owner_username, limit),
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description]
        except Exception:
            return pd.DataFrame()
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([dict(zip(columns, row)) for row in rows], columns=columns)


def _delete_owner_data(owner_username: str) -> None:
    with get_connection() as conn:
        for table_name in OWNED_TABLES:
            conn.execute(f'DELETE FROM "{table_name}" WHERE owner_username = ?', (owner_username,))
        conn.commit()



def show_admin_user_management_panel() -> None:
    current_username = str(st.session_state.get(SESSION_AUTH_USERNAME) or "")
    if not is_admin_username(current_username):
        return

    st.markdown("### Zarządzanie użytkownikami")
    users = list_registered_users()
    if not users:
        st.info("Brak zarejestrowanych użytkowników.")
        return

    summary_rows = []
    for user in users:
        record_count = sum(_count_owner_rows(table_name, user["username"]) for table_name in OWNED_TABLES)
        summary_rows.append(
            {
                "login": user["username"],
                "imię i nazwisko": user["name"],
                "liczba rekordów": record_count,
            }
        )

    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    manageable_users = [user for user in users if not is_admin_username(user["username"])]
    if not manageable_users:
        st.info("Brak użytkowników do zarządzania.")
        return

    selected_username = st.selectbox(
        "Wybierz użytkownika",
        options=[user["username"] for user in manageable_users],
        format_func=lambda username: next(
            (f"{user['name']} ({user['username']})" for user in manageable_users if user["username"] == username),
            str(username),
        ),
        key="admin_user_select",
    )

    selected_user = next(user for user in manageable_users if user["username"] == selected_username)

    selected_table = st.selectbox("Wybierz tabelę do podglądu", options=OWNED_TABLES, key="admin_user_table_select")
    table_count = _count_owner_rows(selected_table, selected_username)
    st.metric("Liczba rekordów", table_count)
    if table_count:
        preview_df = _load_owner_table_preview(selected_table, selected_username)
        if preview_df.empty:
            st.info("Brak podglądu danych.")
        else:
            st.dataframe(preview_df, use_container_width=True, hide_index=True)
    else:
        st.info("Tabela jest pusta dla tego użytkownika.")

    with st.expander("Reset hasła użytkownika"):
        with st.form(key="admin_reset_password_form"):
            new_password = st.text_input("Nowe hasło", type="password", key="admin_reset_password_new")
            confirm_password = st.text_input("Potwierdź nowe hasło", type="password", key="admin_reset_password_confirm")
            submitted_reset = st.form_submit_button("Zapisz nowe hasło")

            if submitted_reset:
                new_password = str(new_password or "").strip()
                confirm_password = str(confirm_password or "").strip()

                if not new_password or not confirm_password:
                    st.error("Wypełnij oba pola hasła.")
                elif new_password != confirm_password:
                    st.error("Hasła nie są identyczne.")
                elif set_registered_user_password(selected_user["username"], new_password):
                    st.success("Hasło użytkownika zostało zresetowane.")
                    st.rerun()

    if st.button("Usuń użytkownika i jego dane", key="admin_delete_user"):
        if delete_registered_user(selected_user["username"]):
            _delete_owner_data(selected_user["username"])
            st.success("Użytkownik został usunięty.")
            st.rerun()


def get_connection() -> Any:
    return db.get_connection()



def render_category_donut_chart(series: pd.Series) -> None:
    if series.empty:
        return

    chart_df = pd.DataFrame({
        "kategoria": series.index.astype(str),
        "wartosc": series.values.astype(float),
    })

    total = float(chart_df["wartosc"].sum())
    if total <= 0:
        return

    chart_df["udział"] = chart_df["wartosc"] / total

    st.markdown(
        "<div style='display:flex; flex-direction:column; align-items:center; gap:0.4rem;'>"
        f"<div style='font-size:13px; font-weight:600; color:#111827;'>Udział kosztów</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    cols = st.columns(min(3, len(chart_df)))
    for idx, row in chart_df.iterrows():
        with cols[idx % len(cols)]:
            progress_value = float(row["udział"] * 100)
            st.progress(progress_value / 100.0)
            st.caption(f"{row['kategoria']}: {progress_value:.1f}%")


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Ewidencja")
    return output.getvalue()


def ensure_column(table_name: str, column_name: str, column_def: str) -> None:
    with get_connection() as conn:
        try:
            conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {column_def}')
        except Exception as exc:
            err = str(exc).lower()
            if "duplicate column" not in err and "already exists" not in err:
                raise


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                area_ha REAL NOT NULL,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS farms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                owner_name TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                notes TEXT,
                is_default INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                farm_id INTEGER NOT NULL,
                field_id INTEGER,
                name TEXT NOT NULL,
                area_ha REAL NOT NULL,
                notes TEXT,
                FOREIGN KEY(farm_id) REFERENCES farms(id),
                FOREIGN KEY(field_id) REFERENCES fields(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS treatments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                batch_id TEXT,
                field_id INTEGER NOT NULL,
                treatment_date TEXT NOT NULL,
                treatment_type TEXT NOT NULL,
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
                products_json TEXT,
                FOREIGN KEY(field_id) REFERENCES fields(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                treatment_id INTEGER NOT NULL,
                cost_type TEXT NOT NULL,
                amount_pln REAL NOT NULL,
                supplier TEXT,
                invoice_no TEXT,
                notes TEXT,
                FOREIGN KEY(treatment_id) REFERENCES treatments(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS field_crop_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL DEFAULT '',
                field_id INTEGER NOT NULL,
                season_id INTEGER NOT NULL,
                crop_id INTEGER NOT NULL,
                notes TEXT,
                FOREIGN KEY(field_id) REFERENCES fields(id),
                FOREIGN KEY(season_id) REFERENCES seasons(id),
                FOREIGN KEY(crop_id) REFERENCES crops(id),
                UNIQUE(field_id, season_id)
            )
            """
        )
        conn.commit()

    for table_name in PRODUCT_TABLES:
        with get_connection() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_username TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    price_per_unit REAL NOT NULL,
                    unit TEXT NOT NULL,
                    notes TEXT
                )
                """
            )
            conn.commit()

    for table_name in OWNED_TABLES:
        ensure_column(table_name, "owner_username", "owner_username TEXT NOT NULL DEFAULT ''")

    ensure_column("plots", "farm_id", "farm_id INTEGER")
    ensure_column("plots", "field_id", "field_id INTEGER")
    ensure_column("plots", "name", "name TEXT")
    ensure_column("plots", "area_ha", "area_ha REAL")
    ensure_column("plots", "notes", "notes TEXT")
    ensure_column("treatments", "product_category", "product_category TEXT")
    ensure_column("treatments", "product_name", "product_name TEXT")
    ensure_column("treatments", "product_unit", "product_unit TEXT")
    ensure_column("treatments", "product_price", "product_price REAL")
    ensure_column("treatments", "crop_id", "crop_id INTEGER")
    ensure_column("treatments", "crop_name", "crop_name TEXT")
    ensure_column("treatments", "products_json", "products_json TEXT")
    ensure_column("treatments", "batch_id", "batch_id TEXT")
    ensure_column('Nawozy', "n_pct", "n_pct REAL DEFAULT 0")
    ensure_column('Nawozy', "p2o5_pct", "p2o5_pct REAL DEFAULT 0")
    ensure_column('Nawozy', "k2o_pct", "k2o_pct REAL DEFAULT 0")
    ensure_column('Nawozy', "so3_pct", "so3_pct REAL DEFAULT 0")
    ensure_column('Nawozy', "cao_pct", "cao_pct REAL DEFAULT 0")
    ensure_column("seasons", "is_default", "is_default INTEGER NOT NULL DEFAULT 0")


def _clear_data_cache() -> None:
    """Invalidates per-user data caches after any write operation."""
    load_fields.clear()
    load_farms.clear()
    load_seasons.clear()
    load_crops.clear()
    load_crop_assignments.clear()
    load_plots.clear()
    load_treatments.clear()
    load_costs.clear()
    load_product_catalog.clear()


def _delete_confirmation(action_key: str, item_label: str) -> bool:
    """Render a confirmation form and return whether deletion was confirmed."""
    if st.session_state.get("pending_delete_action") != action_key:
        return False

    with st.form(f"confirm_delete_{action_key}"):
        st.warning(f"Czy na pewno usunąć: {item_label}?")
        confirm_cols = st.columns(2)
        confirmed = confirm_cols[0].form_submit_button("Potwierdź usunięcie", use_container_width=True)
        cancelled = confirm_cols[1].form_submit_button("Anuluj", use_container_width=True)

    if cancelled:
        st.session_state.pop("pending_delete_action", None)
        st.rerun()
    if confirmed:
        st.session_state.pop("pending_delete_action", None)
        return True
    return False


@st.cache_data(ttl=60, show_spinner=False)
def load_fields(owner: str) -> pd.DataFrame:
    columns = ["id", "name", "area_ha", "notes"]
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, area_ha, notes FROM fields WHERE owner_username = ? ORDER BY name",
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


@st.cache_data(ttl=60, show_spinner=False)
def load_farms(owner: str) -> pd.DataFrame:
    columns = ["id", "name", "notes"]
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, notes FROM farms WHERE owner_username = ? ORDER BY name",
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


@st.cache_data(ttl=60, show_spinner=False)
def load_seasons(owner: str) -> pd.DataFrame:
    columns = ["id", "name", "notes", "is_default"]
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, notes, COALESCE(is_default, 0) AS is_default FROM seasons WHERE owner_username = ? ORDER BY is_default DESC, name",
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


def set_default_season(season_id: Optional[int]) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("UPDATE seasons SET is_default = 0 WHERE owner_username = ?", (owner,))
        if season_id is not None:
            conn.execute(
                "UPDATE seasons SET is_default = 1 WHERE id = ? AND owner_username = ?",
                (season_id, owner),
            )
        conn.commit()
    _clear_data_cache()


@st.cache_data(ttl=60, show_spinner=False)
def load_crops(owner: str) -> pd.DataFrame:
    columns = ["id", "name", "notes"]
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, notes FROM crops WHERE owner_username = ? ORDER BY name",
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


@st.cache_data(ttl=60, show_spinner=False)
def load_crop_assignments(owner: str) -> pd.DataFrame:
    columns = ["id", "field_id", "season_id", "crop_id", "notes", "field_name", "season_name", "crop_name"]
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.field_id,
                a.season_id,
                a.crop_id,
                a.notes,
                f.name AS field_name,
                s.name AS season_name,
                c.name AS crop_name
            FROM field_crop_assignments a
            LEFT JOIN fields f ON f.id = a.field_id
            LEFT JOIN seasons s ON s.id = a.season_id
            LEFT JOIN crops c ON c.id = a.crop_id
            WHERE a.owner_username = ?
            ORDER BY f.name, s.name
            """,
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


def get_crop_assignment(field_id: int, season_id: int) -> Optional[dict]:
    owner = _current_owner()
    columns = ["id", "field_id", "season_id", "crop_id", "crop_name", "notes"]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT a.id, a.field_id, a.season_id, a.crop_id, c.name AS crop_name, a.notes FROM field_crop_assignments a LEFT JOIN crops c ON c.id = a.crop_id WHERE a.field_id = ? AND a.season_id = ? AND a.owner_username = ?",
            (field_id, season_id, owner),
        ).fetchone()
    return dict(zip(columns, row)) if row else None


def get_season_id_by_name(season_name: str) -> Optional[int]:
    owner = _current_owner()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM seasons WHERE name = ? AND owner_username = ?",
            (season_name, owner),
        ).fetchone()
    return int(row[0]) if row else None


def resolve_treatment_crop(field_id: Optional[int], season_name: str, crop_id: Optional[int] = None, crop_name: str = "") -> tuple[Optional[int], str]:
    if crop_name:
        return crop_id, crop_name
    if field_id is None or not season_name:
        return crop_id, crop_name

    season_id = get_season_id_by_name(season_name)
    if season_id is None:
        return crop_id, crop_name

    assignment = get_crop_assignment(int(field_id), season_id)
    if not assignment:
        return crop_id, crop_name

    resolved_crop_id = int(assignment["crop_id"]) if assignment.get("crop_id") is not None else None
    resolved_crop_name = str(assignment.get("crop_name") or "")
    return resolved_crop_id, resolved_crop_name


def parse_products_payload(products_json: Optional[str]) -> list[dict]:
    if not products_json:
        return []
    try:
        parsed = json.loads(str(products_json))
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def extract_user_notes(notes: Optional[str]) -> str:
    if not notes:
        return ""
    text = str(notes)
    parts = [part for part in text.splitlines()]
    if not parts:
        return ""

    last_line = parts[-1].strip()
    if last_line:
        try:
            parsed = json.loads(last_line)
            if isinstance(parsed, list):
                return "\n".join(parts[:-1]).strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return text


def build_treatment_group_key(row: dict) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("treatment_date") or ""),
        str(row.get("season") or row.get("treatment_type") or ""),
        str(row.get("product_category") or ""),
        str(row.get("product_name") or row.get("product") or ""),
        str(row.get("dose") or ""),
        str(row.get("notes") or ""),
    )


def get_treatment_share_factors(treatments_df: pd.DataFrame) -> dict[int, float]:
    if treatments_df.empty:
        return {}

    grouped_rows: dict[tuple[str, str, str, str, str, str], list[dict]] = {}
    for _, row in treatments_df.iterrows():
        row_dict = row.to_dict()
        grouped_rows.setdefault(build_treatment_group_key(row_dict), []).append(row_dict)

    share_factors: dict[int, float] = {}
    for group_rows in grouped_rows.values():
        group_field_areas = {
            int(row["id"]): float(get_field_plot_area(int(row["field_id"])))
            for row in group_rows
            if pd.notna(row.get("field_id"))
        }
        total_group_area = float(sum(group_field_areas.values()))
        stored_areas = [float(row.get("area_ha") or 0.0) for row in group_rows]
        duplicated_total_area = (
            len(group_rows) > 1
            and total_group_area > 0
            and stored_areas
            and all(abs(area - total_group_area) <= 0.01 for area in stored_areas)
        )

        for row in group_rows:
            treatment_id = int(row["id"])
            if duplicated_total_area and treatment_id in group_field_areas:
                share_factors[treatment_id] = group_field_areas[treatment_id] / total_group_area
            else:
                share_factors[treatment_id] = 1.0

    return share_factors


def prepare_treatments_for_reports(treatments_df: pd.DataFrame, costs_df: pd.DataFrame) -> pd.DataFrame:
    if treatments_df.empty:
        return pd.DataFrame()

    treatment_costs = (
        costs_df.groupby("treatment_id", dropna=False)["amount_pln"]
        .sum()
        .reset_index()
    )

    report_df = treatments_df.merge(
        treatment_costs,
        how="left",
        left_on="id",
        right_on="treatment_id",
    )
    report_df["amount_pln"] = report_df["amount_pln"].fillna(0.0)
    report_df["area_ha"] = pd.to_numeric(report_df["area_ha"], errors="coerce").fillna(0.0)
    report_df["treatment_cost_pln"] = pd.to_numeric(report_df["amount_pln"], errors="coerce").fillna(0.0)

    share_factors = get_treatment_share_factors(report_df)
    report_df["report_share_factor"] = report_df["id"].map(lambda treatment_id: float(share_factors.get(int(treatment_id), 1.0)))
    report_df["treatment_cost_pln"] = report_df["treatment_cost_pln"] * report_df["report_share_factor"]
    report_df["area_ha"] = report_df["area_ha"] * report_df["report_share_factor"]
    report_df["report_crop_name"] = report_df.apply(
        lambda row: resolve_treatment_crop(
            int(row["field_id"]) if pd.notna(row.get("field_id")) else None,
            str(row.get("season") or ""),
            int(row["crop_id"]) if pd.notna(row.get("crop_id")) else None,
            str(row.get("crop_name") or ""),
        )[1],
        axis=1,
    )
    return report_df


def save_crop_assignment(field_id: int, season_id: int, crop_id: Optional[int], notes: str = "") -> None:
    owner = _current_owner()
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM field_crop_assignments WHERE field_id = ? AND season_id = ? AND owner_username = ?",
            (field_id, season_id, owner),
        ).fetchone()
        if crop_id is None:
            if existing:
                conn.execute("DELETE FROM field_crop_assignments WHERE id = ? AND owner_username = ?", (existing[0], owner))
        else:
            if existing:
                conn.execute(
                    "UPDATE field_crop_assignments SET crop_id = ?, notes = ? WHERE id = ? AND owner_username = ?",
                    (crop_id, notes, existing[0], owner),
                )
            else:
                conn.execute(
                    "INSERT INTO field_crop_assignments (owner_username, field_id, season_id, crop_id, notes) VALUES (?, ?, ?, ?, ?)",
                    (owner, field_id, season_id, crop_id, notes),
                )
        conn.commit()
    _clear_data_cache()


def build_crop_rotation_table(
    fields_df: pd.DataFrame,
    seasons_df: pd.DataFrame,
    assignments_df: pd.DataFrame,
    selected_season_names: list[str],
) -> pd.DataFrame:
    if fields_df.empty:
        return pd.DataFrame(columns=["field_id", "pole", *selected_season_names])

    has_assignment_columns = (
        not assignments_df.empty
        and "field_id" in assignments_df.columns
        and "season_name" in assignments_df.columns
    )
    has_crop_name_column = has_assignment_columns and "crop_name" in assignments_df.columns

    rows = []
    for _, field in fields_df.iterrows():
        row = {"field_id": int(field["id"]), "pole": str(field["name"])}
        for season_name in selected_season_names:
            if has_assignment_columns:
                assignment = assignments_df[
                    (assignments_df["field_id"] == int(field["id"])) &
                    (assignments_df["season_name"].fillna("") == season_name)
                ]
                if not assignment.empty and has_crop_name_column and pd.notna(assignment["crop_name"].iloc[0]):
                    row[season_name] = str(assignment["crop_name"].iloc[0])
                else:
                    row[season_name] = ""
            else:
                row[season_name] = ""
        rows.append(row)

    return pd.DataFrame(rows)


def render_crop_rotation_progress_charts(
    edited_rotation_df: pd.DataFrame,
    selected_season_names: list[str],
    fields_df: pd.DataFrame,
) -> None:
    if edited_rotation_df.empty:
        return

    field_area_lookup = {
        int(row["id"]): float(get_field_plot_area(int(row["id"])))
        for _, row in fields_df.iterrows()
    }
    total_all_fields_area = sum(field_area_lookup.values())

    if total_all_fields_area <= 0:
        return

    for season_name in selected_season_names:
        crop_area_totals = {}
        for _, row in edited_rotation_df.iterrows():
            crop_name = str(row.get(season_name, "") or "").strip()
            if not crop_name:
                continue
            field_id = int(row["field_id"])
            crop_area_totals[crop_name] = crop_area_totals.get(crop_name, 0.0) + field_area_lookup.get(field_id, 0.0)

        if not crop_area_totals:
            st.info(f"Brak przypisań upraw dla sezonu {season_name}.")
            continue

        st.markdown(f"#### {season_name}")
        st.caption(f"Suma powierzchni wszystkich pól: {total_all_fields_area:.2f} ha")

        crop_items = sorted(crop_area_totals.items(), key=lambda item: item[1], reverse=True)
        if crop_items:
            cols = st.columns(len(crop_items))
            for idx, (crop_name, crop_area) in enumerate(crop_items):
                share_pct = (crop_area / total_all_fields_area * 100.0) if total_all_fields_area > 0 else 0.0
                with cols[idx]:
                    st.write(f"**{crop_name}**")
                    st.progress(share_pct / 100.0)
                    st.caption(f"{crop_area:.2f} ha • {share_pct:.1f}%")
        st.divider()


@st.cache_data(ttl=60, show_spinner=False)
def load_plots(owner: str) -> pd.DataFrame:
    columns = ["id", "farm_id", "field_id", "name", "area_ha", "notes", "farm_name", "field_name"]
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.farm_id,
                p.field_id,
                p.name,
                p.area_ha,
                p.notes,
                f.name AS farm_name,
                fld.name AS field_name
            FROM plots p
            LEFT JOIN farms f ON f.id = p.farm_id
            LEFT JOIN fields fld ON fld.id = p.field_id
            WHERE p.owner_username = ?
            ORDER BY p.name
            """,
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


@st.cache_data(ttl=60, show_spinner=False)
def load_treatments(owner: str) -> pd.DataFrame:
    columns = [
        "id",
        "batch_id",
        "field_id",
        "treatment_date",
        "season",
        "product",
        "product_category",
        "product_name",
        "product_unit",
        "product_price",
        "dose",
        "area_ha",
        "crop_id",
        "crop_name",
        "products_json",
        "field_name",
        "field_area_ha",
        "notes",
    ]
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                t.id,
                t.batch_id,
                t.field_id,
                t.treatment_date,
                t.treatment_type AS season,
                t.product,
                t.product_category,
                t.product_name,
                t.product_unit,
                t.product_price,
                t.dose,
                t.area_ha,
                t.crop_id,
                t.crop_name,
                t.products_json,
                f.name AS field_name,
                f.area_ha AS field_area_ha,
                t.notes
            FROM treatments t
            LEFT JOIN fields f ON f.id = t.field_id
            WHERE t.owner_username = ?
            ORDER BY t.treatment_date DESC, t.id DESC
            """,
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


def build_treatment_list_groups(treatments_df: pd.DataFrame) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for _, row in treatments_df.iterrows():
        row_dict = row.to_dict()
        batch_id = str(row_dict.get("batch_id") or "").strip()
        group_key = batch_id or f"legacy-{int(row_dict['id'])}"
        groups.setdefault(group_key, []).append(row_dict)

    grouped_rows = []
    for group_key, rows in groups.items():
        first_row = rows[0]
        field_names = list(dict.fromkeys(str(row.get("field_name") or "-") for row in rows))
        product_names = [
            str(product.get("product_name") or "").strip()
            for product in parse_treatment_products(first_row.get("notes"), first_row)
            if str(product.get("product_name") or "").strip()
        ]
        grouped_rows.append(
            {
                "id": str(first_row.get("batch_id") or int(first_row["id"])),
                "group_key": group_key,
                "batch_id": str(first_row.get("batch_id") or ""),
                "treatment_ids": [int(row["id"]) for row in rows],
                "treatment_date": str(first_row.get("treatment_date") or ""),
                "season": str(first_row.get("season") or ""),
                "field_name": ", ".join(field_names),
                "total_area_ha": sum(float(get_field_plot_area(int(row["field_id"]))) for row in rows),
                "product_name": ", ".join(product_names) or str(first_row.get("product_name") or first_row.get("product") or "-"),
                "notes": str(first_row.get("notes") or ""),
                "fields": rows,
                "products": parse_treatment_products(first_row.get("notes"), first_row),
            }
        )
    return sorted(grouped_rows, key=lambda row: (row["treatment_date"], row["id"]), reverse=True)


@st.cache_data(ttl=60, show_spinner=False)
def load_costs(owner: str) -> pd.DataFrame:
    columns = ["id", "treatment_id", "cost_type", "amount_pln", "supplier", "invoice_no", "notes", "treatment_date", "treatment_type", "field_name"]
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.treatment_id,
                c.cost_type,
                c.amount_pln,
                c.supplier,
                c.invoice_no,
                c.notes,
                t.treatment_date,
                t.treatment_type,
                f.name AS field_name
            FROM costs c
            LEFT JOIN treatments t ON t.id = c.treatment_id
            LEFT JOIN fields f ON f.id = t.field_id
            WHERE c.owner_username = ?
            ORDER BY c.id DESC
            """,
            (owner,),
        ).fetchall()
    return _dataframe_from_rows([dict(zip(columns, row)) for row in rows], columns)


def build_report_display_rows(report_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, treatment in report_df.iterrows():
        treatment_cost = float(treatment.get("treatment_cost_pln") or 0.0)
        share_factor = float(treatment.get("report_share_factor") or 1.0)
        effective_crop_name = str(treatment.get("report_crop_name") or treatment.get("crop_name") or "")
        products = parse_treatment_products(treatment.get("notes"), treatment.to_dict())
        if not products:
            products = [{
                "category": treatment.get("product_category") or PRODUCT_TABLES[0],
                "product_name": treatment.get("product_name") or treatment.get("product") or "",
                "price_per_unit": float(treatment.get("product_price") or 0.0),
                "unit": treatment.get("product_unit") or "",
                "dose": treatment.get("dose") or 0.0,
                "area_ha": treatment.get("area_ha") or 0.0,
            }]

        for index, product in enumerate(products):
            area_ha = float(product.get("area_ha") or 0.0) * share_factor
            product_cost_pln = round(
                parse_dose_value(str(product.get("dose") or 0)) * max(area_ha, 0.0) * max(float(product.get("price_per_unit") or 0.0), 0.0),
                2,
            )
            cost_per_ha = (product_cost_pln / area_ha) if area_ha > 0 else 0.0
            rows.append({
                "treatment_date": treatment.get("treatment_date") if index == 0 else "",
                "field_name": treatment.get("field_name") if index == 0 else "",
                "season": treatment.get("season") if index == 0 else "",
                "crop_name": effective_crop_name if index == 0 else "",
                "product_category": product.get("category") or product.get("product_category") or "",
                "product_name": product.get("product_name") or "",
                "dose": product.get("dose") or 0.0,
                "area_ha": area_ha,
                "treatment_cost_pln": treatment_cost if index == 0 else "",
                "product_cost_pln": product_cost_pln,
                "cost_per_ha_pln": cost_per_ha,
                "notes": treatment.get("notes") or "",
            })

    return pd.DataFrame(rows)


def build_fertilizer_nutrient_summary(report_df: pd.DataFrame, reference_area_ha: float) -> pd.DataFrame:
    if report_df.empty:
        return pd.DataFrame()

    if "product_category" not in report_df.columns:
        return pd.DataFrame()

    fertilizer_rows = report_df[
        report_df["product_category"].fillna("").astype(str).str.strip().str.casefold() == "nawozy"
    ].copy()
    if fertilizer_rows.empty:
        return pd.DataFrame()

    nawozy_catalog = load_product_catalog("Nawozy", _current_owner())
    if nawozy_catalog.empty:
        return pd.DataFrame()

    catalog_by_name = {
        str(row.get("name") or "").strip().casefold(): row
        for _, row in nawozy_catalog.iterrows()
        if str(row.get("name") or "").strip()
    }

    nutrient_totals_kg = {
        "N": 0.0,
        "P2O5": 0.0,
        "K2O": 0.0,
        "SO3": 0.0,
        "CaO": 0.0,
    }

    for _, row in fertilizer_rows.iterrows():
        product_name_key = str(row.get("product_name") or "").strip().casefold()
        catalog_row = catalog_by_name.get(product_name_key)
        if catalog_row is None:
            continue

        dose_value = max(parse_dose_value(str(row.get("dose") or "0")), 0.0)
        area_ha = max(float(row.get("area_ha") or 0.0), 0.0)
        product_amount = dose_value * area_ha
        unit_value = str(catalog_row.get("unit") or "").strip().casefold()
        if unit_value == "t":
            product_amount *= 1000.0

        nutrient_totals_kg["N"] += product_amount * max(float(catalog_row.get("n_pct") or 0.0), 0.0) / 100.0
        nutrient_totals_kg["P2O5"] += product_amount * max(float(catalog_row.get("p2o5_pct") or 0.0), 0.0) / 100.0
        nutrient_totals_kg["K2O"] += product_amount * max(float(catalog_row.get("k2o_pct") or 0.0), 0.0) / 100.0
        nutrient_totals_kg["SO3"] += product_amount * max(float(catalog_row.get("so3_pct") or 0.0), 0.0) / 100.0
        nutrient_totals_kg["CaO"] += product_amount * max(float(catalog_row.get("cao_pct") or 0.0), 0.0) / 100.0

    area_reference = max(float(reference_area_ha or 0.0), 0.0)
    divisor = area_reference if area_reference > 0 else 1.0

    return pd.DataFrame([
        {
            "N [kg/ha]": round(nutrient_totals_kg["N"] / divisor, 2),
            "P2O5 [kg/ha]": round(nutrient_totals_kg["P2O5"] / divisor, 2),
            "K2O [kg/ha]": round(nutrient_totals_kg["K2O"] / divisor, 2),
            "SO3 [kg/ha]": round(nutrient_totals_kg["SO3"] / divisor, 2),
            "CaO [kg/ha]": round(nutrient_totals_kg["CaO"] / divisor, 2),
        }
    ])


def build_field_report(field_id: int, season_name: str, treatments_df: pd.DataFrame, costs_df: pd.DataFrame) -> tuple[pd.DataFrame, float, float]:
    required_columns = {"field_id", "season"}
    if treatments_df.empty or not required_columns.issubset(treatments_df.columns):
        return pd.DataFrame(), 0.0, 0.0

    filtered_treatments = treatments_df[
        (treatments_df["field_id"] == field_id) & (treatments_df["season"] == season_name)
    ].copy()
    if filtered_treatments.empty:
        return pd.DataFrame(), 0.0, 0.0

    report_df = prepare_treatments_for_reports(filtered_treatments, costs_df)
    total_cost = float(report_df["treatment_cost_pln"].sum())
    field_area = get_field_plot_area(field_id)
    cost_per_ha = total_cost / field_area if field_area > 0 else 0.0

    display_df = build_report_display_rows(report_df)
    return display_df[[
        "treatment_date",
        "field_name",
        "season",
        "product_category",
        "product_name",
        "dose",
        "area_ha",
        "treatment_cost_pln",
        "product_cost_pln",
        "cost_per_ha_pln",
        "notes",
    ]], total_cost, cost_per_ha


def build_crop_report(crop_name: str, season_name: str, treatments_df: pd.DataFrame, costs_df: pd.DataFrame) -> tuple[pd.DataFrame, float, float, float]:
    required_columns = {"season", "field_id"}
    if treatments_df.empty or not required_columns.issubset(treatments_df.columns):
        return pd.DataFrame(), 0.0, 0.0, 0.0

    season_treatments = treatments_df[
        treatments_df["season"].fillna("") == season_name
    ].copy()
    if season_treatments.empty:
        return pd.DataFrame(), 0.0, 0.0, 0.0

    prepared_treatments = prepare_treatments_for_reports(season_treatments, costs_df)
    report_df = prepared_treatments[
        prepared_treatments["report_crop_name"].fillna("") == crop_name
    ].copy()
    if report_df.empty:
        return pd.DataFrame(), 0.0, 0.0, 0.0

    total_cost = float(report_df["treatment_cost_pln"].sum())
    unique_field_ids = {
        int(field_id)
        for field_id in report_df["field_id"].dropna().tolist()
    }
    total_area_ha = float(sum(get_field_plot_area(field_id) for field_id in unique_field_ids))
    cost_per_ha = total_cost / total_area_ha if total_area_ha > 0 else 0.0

    display_df = build_report_display_rows(report_df)
    return display_df[[
        "treatment_date",
        "field_name",
        "season",
        "crop_name",
        "product_category",
        "product_name",
        "dose",
        "area_ha",
        "treatment_cost_pln",
        "product_cost_pln",
        "cost_per_ha_pln",
        "notes",
    ]], total_area_ha, total_cost, cost_per_ha


def build_treatment_registry_report(
    farm_id: int,
    season_name: str,
    farms_df: pd.DataFrame,
    plots_df: pd.DataFrame,
    fields_df: pd.DataFrame,
    treatments_df: pd.DataFrame,
    category_filter: Optional[str] = None,
    group_fields: bool = False,
) -> pd.DataFrame:
    required_columns = {"field_id", "season"}
    if treatments_df.empty or plots_df.empty or farms_df.empty:
        return pd.DataFrame()
    if not required_columns.issubset(treatments_df.columns) or "farm_name" not in plots_df.columns or "field_id" not in plots_df.columns or "name" not in farms_df.columns:
        return pd.DataFrame()

    farm_row = farms_df[farms_df["id"] == farm_id]
    if farm_row.empty:
        return pd.DataFrame()
    farm_name = farm_row["name"].iloc[0]
    selected_plots = plots_df[plots_df["farm_name"] == farm_name].copy()
    if selected_plots.empty:
        return pd.DataFrame()

    plot_field_ids = selected_plots["field_id"].dropna().astype(int).tolist()
    filtered_treatments = treatments_df[
        (treatments_df["field_id"].isin(plot_field_ids)) & (treatments_df["season"] == season_name)
    ].copy()
    if filtered_treatments.empty:
        return pd.DataFrame()

    rows = []
    for _, treatment in filtered_treatments.iterrows():
        if pd.isna(treatment["field_id"]):
            continue
        field_id = int(treatment["field_id"])
        plot_rows = selected_plots[selected_plots["field_id"] == field_id]
        if plot_rows.empty:
            plot_rows = pd.DataFrame([{"name": "", "area_ha": 0.0}])

        products = parse_treatment_products(treatment.get("notes"), treatment)
        if not products:
            products = [{
                "product_category": treatment.get("product_category") or "",
                "product_name": treatment.get("product_name") or treatment.get("product") or "",
                "dose": treatment.get("dose") or 0.0,
            }]

        total_field_area = float(plot_rows["area_ha"].sum()) if not plot_rows.empty else 0.0
        treatment_area = float(treatment.get("area_ha") or 0.0)
        area_ratio = 1.0
        if total_field_area > 0 and treatment_area > 0 and not math.isclose(treatment_area, total_field_area, rel_tol=1e-9, abs_tol=1e-9):
            area_ratio = treatment_area / total_field_area

        for _, plot_row in plot_rows.iterrows():
            plot_name = plot_row["name"]
            plot_area = float(plot_row["area_ha"] or 0.0) * area_ratio
            crop_name = treatment.get("crop_name") or ""
            for product in products:
                rows.append(
                    {
                        "plot_name": plot_name,
                        "treatment_date": treatment.get("treatment_date"),
                        "season": treatment.get("season"),
                        "uprawa": crop_name,
                        "product_category": product.get("category") or product.get("product_category") or "",
                        "product_name": product.get("product_name") or "",
                        "dose": product.get("dose") or 0.0,
                        "area_ha": plot_area,
                    }
                )

    report_df = pd.DataFrame(rows)
    if report_df.empty:
        return report_df
    if category_filter:
        report_df = report_df[report_df["product_category"] == category_filter].copy()

    if report_df.empty:
        return report_df

    if group_fields:
        group_cols = ["treatment_date", "season", "uprawa", "product_category", "product_name", "dose"]
        return (
            report_df.groupby(group_cols, dropna=False, as_index=False)
            .agg(
                plot_name=("plot_name", lambda values: ", ".join(str(value) for value in dict.fromkeys(values) if str(value).strip())),
                area_ha=("area_ha", "sum"),
            )
            .sort_values(by=["treatment_date", "uprawa", "product_name", "dose"], kind="stable")
            .reset_index(drop=True)
        )

    return report_df


def build_product_consumption_report(
    treatments_df: pd.DataFrame,
    date_from: date,
    date_to: date,
) -> pd.DataFrame:
    columns = ["product_category", "product_name", "quantity", "unit"]
    if treatments_df.empty or date_from > date_to:
        return pd.DataFrame(columns=columns)

    treatment_dates = pd.to_datetime(treatments_df["treatment_date"], errors="coerce")
    filtered_treatments = treatments_df[
        treatment_dates.notna()
        & (treatment_dates.dt.date >= date_from)
        & (treatment_dates.dt.date <= date_to)
    ]
    if filtered_treatments.empty:
        return pd.DataFrame(columns=columns)

    share_factors = get_treatment_share_factors(filtered_treatments)
    rows = []
    for _, treatment in filtered_treatments.iterrows():
        share_factor = float(share_factors.get(int(treatment["id"]), 1.0))
        for product in parse_treatment_products(treatment.get("notes"), treatment.to_dict()):
            product_name = str(product.get("product_name") or "").strip()
            if not product_name:
                continue
            quantity = (
                parse_dose_value(str(product.get("dose") or 0.0))
                * max(float(product.get("area_ha") or 0.0), 0.0)
                * share_factor
            )
            rows.append(
                {
                    "product_category": str(product.get("category") or ""),
                    "product_name": product_name,
                    "quantity": quantity,
                    "unit": str(product.get("unit") or ""),
                }
            )

    if not rows:
        return pd.DataFrame(columns=columns)

    report_df = pd.DataFrame(rows)
    report_df = (
        report_df.groupby(columns[:2] + ["unit"], as_index=False, dropna=False)["quantity"]
        .sum()
    )
    report_df["quantity"] = report_df["quantity"].round(2)
    return report_df[report_df["quantity"] > 0].sort_values(
        by=["product_name", "product_category", "unit"], kind="stable"
    ).reset_index(drop=True)


@st.cache_data(ttl=60, show_spinner=False)
def load_product_catalog(table_name: str, owner: str) -> pd.DataFrame:
    base_columns = ["id", "name", "price_per_unit", "unit", "notes"]
    nawoz_columns = base_columns + ["n_pct", "p2o5_pct", "k2o_pct", "so3_pct", "cao_pct"]
    try:
        with get_connection() as conn:
            if table_name == "Nawozy":
                rows = conn.execute(
                    "SELECT id, name, price_per_unit, unit, notes,"
                    " COALESCE(n_pct, 0) AS n_pct, COALESCE(p2o5_pct, 0) AS p2o5_pct,"
                    " COALESCE(k2o_pct, 0) AS k2o_pct, COALESCE(so3_pct, 0) AS so3_pct,"
                    ' COALESCE(cao_pct, 0) AS cao_pct FROM "Nawozy" WHERE owner_username = ? ORDER BY name',
                    (owner,),
                ).fetchall()
                columns = nawoz_columns
            else:
                rows = conn.execute(
                    f'SELECT id, name, price_per_unit, unit, notes FROM "{table_name}" WHERE owner_username = ? ORDER BY name',
                    (owner,),
                ).fetchall()
                columns = base_columns
    except Exception:
        columns = nawoz_columns if table_name == "Nawozy" else base_columns
        rows = []
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([dict(zip(columns, row)) for row in rows], columns=columns)



def resolve_sor_registry_path() -> str:
    candidates = [
        SOREGISTRY_PATH,
        os.path.join(os.getcwd(), "rejestr_sor_20260720.xlsx"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "rejestr_sor_20260720.xlsx"),
        os.path.join(os.path.abspath(os.getcwd()), "rejestr_sor_20260720.xlsx"),
    ]
    for candidate_path in candidates:
        if os.path.exists(candidate_path):
            return candidate_path
    return SOREGISTRY_PATH


def load_sor_registry() -> tuple[pd.DataFrame, str, Optional[str]]:
    registry_path = resolve_sor_registry_path()
    if not os.path.exists(registry_path):
        return pd.DataFrame(), registry_path, "Nie znaleziono pliku rejestr_sor_20260720.xlsx."
    try:
        df = pd.read_excel(registry_path, engine="openpyxl")
        return df, registry_path, None
    except Exception as exc:
        try:
            df = pd.read_excel(registry_path)
            return df, registry_path, None
        except Exception as exc2:
            return pd.DataFrame(), registry_path, str(exc2)


def find_sor_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    if df.empty:
        return None
    columns = list(df.columns)
    for candidate in candidates:
        if candidate in columns:
            return candidate
    lower_columns = {col.lower(): col for col in columns}
    for candidate in candidates:
        key = candidate.lower()
        if key in lower_columns:
            return lower_columns[key]
    for candidate in candidates:
        key = candidate.lower()
        for col in columns:
            if key in col.lower():
                return col
    return None


def search_sor_items(registry_df: pd.DataFrame, query: str) -> pd.DataFrame:
    if registry_df.empty or not query:
        return pd.DataFrame()
    name_col = find_sor_column(registry_df, ["Nazwa środka ochrony roślin", "Nazwa środka", "Nazwa środka ochrony roślin "])
    if not name_col:
        return pd.DataFrame()
    matches = registry_df[registry_df[name_col].astype(str).str.contains(query, case=False, na=False)].copy()
    if matches.empty:
        matches = registry_df[registry_df.apply(lambda row: row.astype(str).str.contains(query, case=False, na=False).any(), axis=1)].copy()
    return matches


def get_sor_product_notes(row: pd.Series) -> str:
    notes = []
    for label, candidates in [
        ("Rodzaj środka", ["Rodzaj środka", "Rodzaj srodka", "Rodzaj środka ochrony roślin"]),
        ("Substancja czynna", ["Zawartość, nazwa zwyczajowa substancji czynnej środka ochrony roślin", "Zawartość substancji czynnej", "Substancja czynna"]),
        ("Nr zezwolenia", ["Nr zezwolenia na dopuszczenie do obrotu środka ochrony roślin", "Numer zezwolenia", "Nr zezwolenia"]),
        ("Okres zużycia zapasów", ["Okres na zużycie istniejących zapasów środka ochrony roślin dla sprzedaży i dystrybucji", "Okres na zużycie zapasów", "Okres zużycia"]),
    ]:
        col = find_sor_column(row.to_frame().T, candidates)
        if col:
            value = row.get(col)
            if pd.notna(value) and str(value).strip():
                notes.append(f"{label}: {value}")
    return "\n".join(notes).strip()


def sor_product_exists(name: str) -> bool:
    if not name:
        return False
    df = load_product_catalog("ŚOR", _current_owner())
    if df.empty or "name" not in df.columns:
        return False
    return not df[df["name"].astype(str).str.lower() == name.lower()].empty


def _get_groq_config() -> str:
    """Pobiera klucz API Groq z secrets lub zmiennych środowiska."""
    api_key = ""
    # Streamlit Cloud secrets mogą zawierać wartość jako TOML/JSON lub pustą wartość,
    # więc najpierw próbujemy odczytać z secrets, potem z environment.
    try:
        api_key = str(st.secrets.get("GROQ_API_KEY", "") or "").strip()
    except Exception:
        api_key = ""
    if not api_key:
        api_key = str(os.environ.get("GROQ_API_KEY", "") or "").strip()

    # Obsługa złego formatu: {'GROQ_API_KEY': 'gsk_xxx'} lub inne niepoprawne wklejenie
    if api_key.startswith('{'):
        match = re.search(r"'gsk_[^']*'|\"gsk_[^\"]*\"", api_key)
        if match:
            api_key = match.group(0).strip("'\"")

    return api_key


def _get_google_search_config() -> tuple[str, str]:
    """Pobiera dane do wbudowanego wyszukiwania web dla etykiet, bez ekspozycji w UI."""
    api_key = ""
    engine_id = ""

    try:
        api_key = str(st.secrets.get("GOOGLE_SEARCH_API_KEY", "") or "").strip()
    except Exception:
        api_key = ""
    if not api_key:
        api_key = str(os.environ.get("GOOGLE_SEARCH_API_KEY", "") or "").strip()

    try:
        engine_id = str(st.secrets.get("GOOGLE_SEARCH_ENGINE_ID", "") or "").strip()
    except Exception:
        engine_id = ""
    if not engine_id:
        engine_id = str(os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "") or "").strip()

    return api_key, engine_id


def _web_search_label(query: str, max_results: int = 5) -> list[str]:
    """Wyszukuje etykiety środka w internecie i zwraca krótki kontekst do modelu."""
    api_key, engine_id = _get_google_search_config()
    if not api_key or not engine_id:
        return []

    params = parse.urlencode({
        "key": api_key,
        "cx": engine_id,
        "q": query,
        "num": max_results,
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    try:
        with request.urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"[ERROR] Google Search API exception: {exc}")
        try:
            import traceback
            print(f"[TRACEBACK] {traceback.format_exc()}")
        except Exception:
            pass
        return []

    if "error" in payload:
        error_payload = payload.get("error") or {}
        error_message = error_payload.get("message") or str(error_payload)
        print(f"[ERROR] Google Search API returned error: {error_message}")
        return []

    results = []
    for item in payload.get("items") or []:
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        link = str(item.get("link") or "").strip()
        parts = [part for part in [title, snippet, link] if part]
        if parts:
            results.append(" | ".join(parts))
    return results


def _extract_json_from_text(raw_text: str) -> dict:
    text = str(raw_text or "").strip()
    if not text:
        return {"overall_status": "unknown", "summary": "Brak odpowiedzi modelu AI.", "checks": []}

    if "```" in text:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
        if fenced:
            text = fenced.group(1)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    return {"overall_status": "unknown", "summary": text[:500], "checks": []}


def _get_groq_model_name() -> str:
    """Zwraca stały model AI używany przez aplikację."""
    return "openai/gpt-oss-120b"


def _get_groq_model_candidates() -> list[str]:
    """Lista modeli Groq do próby, w kolejności od najbardziej kompatybilnych."""
    preferred = _get_groq_model_name().strip()
    candidates = [preferred]
    fallback_models = [
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "mixtral-8x7b-32768",
    ]
    for model in fallback_models:
        if model not in candidates:
            candidates.append(model)
    return [m for m in candidates if m]


def analyze_sor_row_with_groq(
    product_name: str,
    crop_name: str,
    dose: Any,
    application_date: Any,
    sor_notes: Any,
) -> dict:
    """Analizuje wiersz SOR za pomocą Groq (darmowe API)."""
    logs = []
    api_key = _get_groq_config()
    if not api_key:
        logs.append("[ERROR] Brak klucza GROQ_API_KEY")
        return {
            "overall_status": "unknown",
            "summary": "Brak klucza GROQ_API_KEY w secrets lub środowisku.",
            "checks": [],
            "debug_logs": logs,
        }

    try:
        from groq import Groq
    except ImportError:
        logs.append("[ERROR] Brak biblioteki groq")
        return {
            "overall_status": "unknown",
            "summary": "Brak biblioteki groq. Zainstaluj zależność: pip install groq",
            "checks": [],
            "debug_logs": logs,
        }

    try:
        logs.append(f"[INFO] Klucz API długość: {len(api_key)}")
        logs.append(f"[INFO] Groq key prefix: {api_key[:12] if api_key else 'BRAK'}")
        if api_key.startswith('{') or api_key.startswith('['):
            logs.append("[ERROR] Klucz Groq ma zły format - prawdopodobnie wklejono cały TOML/JSON zamiast samej wartości.")
            return {
                "overall_status": "unknown",
                "summary": "Błąd konfiguracji klucza Groq. W Streamlit Cloud wklej tylko samą wartość, np. GROQ_API_KEY = \"gsk_xxx\".",
                "checks": [],
                "debug_logs": logs,
            }
        if not api_key.startswith('gsk_'):
            logs.append("[WARN] Klucz Groq nie zaczyna się od gsk_ - sprawdź, czy to właściwy klucz Groq.")

        client = Groq(api_key=api_key)
        logs.append("[INFO] Klient Groq zainicjalizowany")

        search_query = f'"{product_name}" etykieta'
        api_key_search, engine_id = _get_google_search_config()
        logs.append(f"[INFO] Web search query: {search_query}")
        logs.append(f"[INFO] Google Search API key present: {bool(api_key_search)}")
        logs.append(f"[INFO] Google Search engine ID present: {bool(engine_id)}")
        if not api_key_search or not engine_id:
            logs.append("[WARN] Brak GOOGLE_SEARCH_API_KEY lub GOOGLE_SEARCH_ENGINE_ID — web search zostanie pominięty.")
        search_results = _web_search_label(search_query, max_results=5)
        logs.append(f"[INFO] Wyniki web search: {len(search_results)}")
        if search_results:
            for i, result in enumerate(search_results[:3], 1):
                logs.append(f"[WEB {i}] {result[:180]}...")
        else:
            logs.append("[WARN] Brak wyników web search; model musi zwrócić unknown, gdy brak dowodów.")

        web_context = "\n".join(search_results) if search_results else "Brak wiarygodnych wyników web search dla tej etykiety."

        prompt = f"""
Jesteś ekspertem ds. ochrony roślin i zgodności etykiet środków ochrony roślin.

Masz wykonać analizę zgodności na podstawie wyników wyszukiwania web. To nie jest ogólna wiedza — musisz bazować na dostarczonych wynikach i nie zgadywać.

Wyniki wyszukiwania web:
{web_context}

Dane wejściowe:
- nazwa środka: {product_name or 'brak'}
- uprawa docelowa: {crop_name or 'brak'}
- dawka zastosowana: {dose if dose is not None else 'brak'}
- data zastosowania: {application_date if application_date is not None else 'brak'}
- okres zużycia zapasów / karencja z notatek: {sor_notes if sor_notes is not None else 'brak'}

Instrukcje:
1. Używaj tylko wyników web jako dowodów. Jeśli nie ma potwierdzenia z etykiety lub wiarygodnego źródła producenta, nie dopisuj domysłów.
2. Jeśli wyniki web są niepełne, niejasne lub sprzeczne, ustaw overall_status na "unknown" i dokładnie napisz, że brak potwierdzenia.
3. Jeśli etykieta wyraźnie dopuszcza daną uprawę, dawkę i karencję, ustaw overall_status na "compliant".
4. Jeśli etykieta lub źródło producenta wyraźnie pokazuje niezgodność z uprawą, dawką lub karencją, ustaw overall_status na "non_compliant".
5. Nigdy nie zgaduj. Brak dowodów = "unknown".

Wymagany format odpowiedzi JSON (TYLKO JSON, bez komentarza):
{{
  "overall_status": "compliant" | "non_compliant" | "unknown",
  "summary": "krótki opis z podaniem, czy decyzja jest potwierdzona, niepotwierdzona, czy sprzeczna z etykietą",
  "checks": [
    {{"name": "crop_compatibility", "status": "pass" | "fail" | "unknown", "reason": "wyjaśnienie oparte na etykiecie lub brak dowodu"}},
    {{"name": "dose_compliance", "status": "pass" | "fail" | "unknown", "reason": "wyjaśnienie oparte na etykiecie lub brak dowodu"}},
    {{"name": "stock_usage_window", "status": "pass" | "fail" | "unknown", "reason": "wyjaśnienie oparte na karencji lub brak dowodu"}}
  ]
}}

Zasada końcowa: jeśli nie ma wystarczających, zweryfikowalnych dowodów, output ma być "unknown".
"""
        model_candidates = _get_groq_model_candidates()
        logs.append("[INFO] Wysyłanie promptu do Groq...")
        logs.append(f"[INFO] Lista modeli do próby: {model_candidates}")
        
        response = None
        last_error = None
        for model_name in model_candidates:
            try:
                logs.append(f"[INFO] Próba modelu Groq: {model_name}")
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "Jesteś ekspertem ds. ochrony roślin. Odpowiadaj TYLKO w formacie JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0
                )
                logs.append(f"[INFO] Odpowiedź otrzymana z Groq przez model: {model_name}")
                break
            except Exception as model_exc:
                last_error = model_exc
                error_text = str(model_exc)
                logs.append(f"[WARN] Model Groq {model_name} odrzucony: {error_text}")
                if "model_not_found" not in error_text and "model_decommissioned" not in error_text and "not exist" not in error_text:
                    raise
        
        if response is None:
            logs.append("[ERROR] Żaden model Groq nie jest dostępny dla tego konta.")
            logs.append("[ERROR] W Groq Console sprawdź listę aktywnych modeli i ustaw GROQ_MODEL na dokładną nazwę z listy.")
            return {
                "overall_status": "unknown",
                "summary": "Żaden model Groq nie jest dostępny dla tego konta. Sprawdź listę modeli w Groq Console i ustaw GROQ_MODEL na dokładną nazwę z listy.",
                "checks": [],
                "debug_logs": logs,
            }

        response_text = response.choices[0].message.content
        logs.append(f"[INFO] Odpowiedź Groq (pierwsze 200 znaków): {response_text[:200]}")
        
        payload = _extract_json_from_text(response_text)
        logs.append(f"[INFO] Parsowany JSON: {payload}")
        
        if not isinstance(payload, dict):
            logs.append("[ERROR] Payload nie jest słownikiem")
            payload = {"overall_status": "unknown", "summary": "Błąd parsowania odpowiedzi modelu.", "checks": [], "debug_logs": logs}
        
        payload["debug_logs"] = logs
        
        if "checks" not in payload or not isinstance(payload["checks"], list):
            payload["checks"] = []
        if "summary" not in payload or not payload["summary"]:
            payload["summary"] = "Analiza AI zakończona."
        
        logs.append(f"[INFO] Final status: {payload.get('overall_status')}")
        return payload
    except Exception as exc:
        error_str = str(exc)
        logs.append(f"[ERROR] Wyjątek: {error_str}")
        
        import traceback
        logs.append(f"[TRACEBACK] {traceback.format_exc()}")
        return {
            "overall_status": "unknown",
            "summary": f"Błąd połączenia z Groq: {exc}",
            "checks": [],
            "debug_logs": logs,
        }


def import_sor_product(row: pd.Series) -> bool:
    sor_name_col = find_sor_column(row.to_frame().T, ["Nazwa środka ochrony roślin", "Nazwa środka", "Nazwa środka ochrony roślin "])
    if not sor_name_col:
        return False
    product_name = str(row.get(sor_name_col) or "").strip()
    if not product_name:
        return False
    if sor_product_exists(product_name):
        return False
    notes = get_sor_product_notes(row)
    save_product("ŚOR", product_name, 0.0, "", notes)
    return True


def save_field(name: str, area_ha: float, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("INSERT INTO fields (owner_username, name, area_ha, notes) VALUES (?, ?, ?, ?)", (owner, name, area_ha, notes))
        conn.commit()
    _clear_data_cache()


def save_farm(name: str, notes: str, owner_name: str = "") -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("INSERT INTO farms (owner_username, name, owner_name, notes) VALUES (?, ?, ?, ?)", (owner, name, owner_name, notes))
        conn.commit()
    _clear_data_cache()


def save_season(name: str, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        existing_count_row = conn.execute(
            "SELECT COUNT(*) FROM seasons WHERE owner_username = ?",
            (owner,),
        ).fetchone()
        existing_count = int(existing_count_row[0]) if existing_count_row else 0
        is_default = 1 if existing_count == 0 else 0
        conn.execute(
            "INSERT INTO seasons (owner_username, name, notes, is_default) VALUES (?, ?, ?, ?)",
            (owner, name, notes, is_default),
        )
        conn.commit()
    _clear_data_cache()


def update_season(season_id: int, name: str, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("UPDATE seasons SET name = ?, notes = ? WHERE id = ? AND owner_username = ?", (name, notes, season_id, owner))
        conn.commit()
    _clear_data_cache()


def delete_season(season_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("DELETE FROM field_crop_assignments WHERE season_id = ? AND owner_username = ?", (season_id, owner))
        conn.execute(
            "DELETE FROM treatments WHERE owner_username = ? AND treatment_type = (SELECT name FROM seasons WHERE id = ? AND owner_username = ?)",
            (owner, season_id, owner),
        )
        conn.execute("DELETE FROM seasons WHERE id = ? AND owner_username = ?", (season_id, owner))
        conn.commit()
    _clear_data_cache()


def save_crop(name: str, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("INSERT INTO crops (owner_username, name, notes) VALUES (?, ?, ?)", (owner, name, notes))
        conn.commit()
    _clear_data_cache()


def update_crop(crop_id: int, name: str, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("UPDATE crops SET name = ?, notes = ? WHERE id = ? AND owner_username = ?", (name, notes, crop_id, owner))
        conn.commit()
    _clear_data_cache()


def delete_crop(crop_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("DELETE FROM field_crop_assignments WHERE crop_id = ? AND owner_username = ?", (crop_id, owner))
        conn.execute("DELETE FROM treatments WHERE crop_id = ? AND owner_username = ?", (crop_id, owner))
        conn.execute("DELETE FROM crops WHERE id = ? AND owner_username = ?", (crop_id, owner))
        conn.commit()
    _clear_data_cache()


def save_plot(farm_id: int, field_id: Optional[int], name: str, area_ha: float, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO plots (owner_username, farm_id, field_id, name, area_ha, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (owner, farm_id, field_id, name, area_ha, notes),
        )
        conn.commit()
    _clear_data_cache()


def update_field(field_id: int, name: str, area_ha: float, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            "UPDATE fields SET name = ?, area_ha = ?, notes = ? WHERE id = ? AND owner_username = ?",
            (name, area_ha, notes, field_id, owner),
        )
        conn.commit()
    _clear_data_cache()


def update_farm(farm_id: int, name: str, notes: str, owner_name: str = "") -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            "UPDATE farms SET name = ?, owner_name = ?, notes = ? WHERE id = ? AND owner_username = ?",
            (name, owner_name, notes, farm_id, owner),
        )
        conn.commit()
    _clear_data_cache()


def update_plot(plot_id: int, farm_id: int, field_id: Optional[int], name: str, area_ha: float, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            "UPDATE plots SET farm_id = ?, field_id = ?, name = ?, area_ha = ?, notes = ? WHERE id = ? AND owner_username = ?",
            (farm_id, field_id, name, area_ha, notes, plot_id, owner),
        )
        conn.commit()
    _clear_data_cache()


def delete_field(field_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM costs WHERE owner_username = ? AND treatment_id IN (SELECT id FROM treatments WHERE field_id = ? AND owner_username = ?)",
            (owner, field_id, owner),
        )
        conn.execute("DELETE FROM treatments WHERE field_id = ? AND owner_username = ?", (field_id, owner))
        conn.execute("UPDATE plots SET field_id = NULL WHERE field_id = ? AND owner_username = ?", (field_id, owner))
        conn.execute("DELETE FROM fields WHERE id = ? AND owner_username = ?", (field_id, owner))
        conn.commit()
    _clear_data_cache()


def save_product(table_name: str, name: str, price_per_unit: float, unit: str, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            f'INSERT INTO "{table_name}" (owner_username, name, price_per_unit, unit, notes) VALUES (?, ?, ?, ?, ?)',
            (owner, name, price_per_unit, unit, notes),
        )
        conn.commit()
    _clear_data_cache()


def save_nawoz_product(
    name: str,
    price_per_unit: float,
    unit: str,
    notes: str,
    n_pct: float = 0.0,
    p2o5_pct: float = 0.0,
    k2o_pct: float = 0.0,
    so3_pct: float = 0.0,
    cao_pct: float = 0.0,
) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            'INSERT INTO "Nawozy" (owner_username, name, price_per_unit, unit, notes, n_pct, p2o5_pct, k2o_pct, so3_pct, cao_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (owner, name, price_per_unit, unit, notes, n_pct, p2o5_pct, k2o_pct, so3_pct, cao_pct),
        )
        conn.commit()
    _clear_data_cache()


def update_product(table_name: str, product_id: int, name: str, price_per_unit: float, unit: str, notes: str) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            f'UPDATE "{table_name}" SET name = ?, price_per_unit = ?, unit = ?, notes = ? WHERE id = ? AND owner_username = ?',
            (name, price_per_unit, unit, notes, product_id, owner),
        )
        conn.commit()
    _clear_data_cache()


def update_nawoz_product(
    product_id: int,
    name: str,
    price_per_unit: float,
    unit: str,
    notes: str,
    n_pct: float = 0.0,
    p2o5_pct: float = 0.0,
    k2o_pct: float = 0.0,
    so3_pct: float = 0.0,
    cao_pct: float = 0.0,
) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(
            'UPDATE "Nawozy" SET name = ?, price_per_unit = ?, unit = ?, notes = ?, n_pct = ?, p2o5_pct = ?, k2o_pct = ?, so3_pct = ?, cao_pct = ? WHERE id = ? AND owner_username = ?',
            (name, price_per_unit, unit, notes, n_pct, p2o5_pct, k2o_pct, so3_pct, cao_pct, product_id, owner),
        )
        conn.commit()
    _clear_data_cache()


def delete_product(table_name: str, product_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute(f'DELETE FROM "{table_name}" WHERE id = ? AND owner_username = ?', (product_id, owner))
        conn.commit()
    _clear_data_cache()


def delete_farm(farm_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("DELETE FROM plots WHERE farm_id = ? AND owner_username = ?", (farm_id, owner))
        conn.execute("DELETE FROM farms WHERE id = ? AND owner_username = ?", (farm_id, owner))
        conn.commit()
    _clear_data_cache()


def delete_plot(plot_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("DELETE FROM plots WHERE id = ? AND owner_username = ?", (plot_id, owner))
        conn.commit()
    _clear_data_cache()


def parse_dose_value(dose: str) -> float:
    numbers = re.findall(r"[-+]?\d*\.?\d+", dose)
    if not numbers:
        return 0.0
    return float(numbers[0])


def get_field_plot_area(field_id: Optional[int]) -> float:
    if not field_id:
        return 0.0
    owner = _current_owner()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(area_ha), 0) FROM plots WHERE field_id = ? AND owner_username = ?",
            (field_id, owner),
        ).fetchone()
    return float(row[0] or 0.0)


def save_treatments(
    field_ids: list[int],
    treatment_date: str,
    treatment_type: str,
    products: list[dict],
    notes: str,
    crop_id: Optional[int] = None,
    crop_name: str = "",
    batch_id: Optional[str] = None,
) -> int:
    if not field_ids or not products:
        return 0

    valid_field_ids = [field_id for field_id in field_ids if field_id is not None]
    if not valid_field_ids:
        return 0

    owner = _current_owner()
    field_areas = {int(field_id): float(get_field_plot_area(int(field_id))) for field_id in valid_field_ids}
    total_selected_area = float(sum(field_areas.values()))
    equal_share = 1.0 / len(valid_field_ids) if valid_field_ids else 0.0
    inserted_treatments = 0
    treatment_batch_id = str(batch_id or uuid4())

    with get_connection() as conn:
        for field_id in valid_field_ids:
            share_factor = (field_areas[int(field_id)] / total_selected_area) if total_selected_area > 0 else equal_share
            field_products = [
                {
                    **product,
                    "area_ha": round(max(float(product.get("area_ha") or 0.0), 0.0) * share_factor, 4),
                }
                for product in products
            ]
            primary_product = field_products[0]
            primary_dose = str(primary_product["dose"])
            resolved_crop_id, resolved_crop_name = resolve_treatment_crop(field_id, treatment_type, crop_id, crop_name)
            cursor = conn.execute(
                """
                INSERT INTO treatments (
                    owner_username, batch_id, field_id, treatment_date, treatment_type, product, product_category, product_name, product_unit, product_price, dose, area_ha, crop_id, crop_name, notes, products_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner,
                    treatment_batch_id,
                    field_id,
                    treatment_date,
                    treatment_type,
                    primary_product["product_name"],
                    primary_product["category"],
                    primary_product["product_name"],
                    primary_product["unit"],
                    primary_product["price_per_unit"],
                    primary_dose,
                    primary_product["area_ha"],
                    resolved_crop_id,
                    resolved_crop_name,
                    str(notes or ""),
                    json.dumps(field_products, ensure_ascii=False),
                ),
            )
            treatment_id = cursor.lastrowid
            inserted_treatments += 1

            for product in field_products:
                dose_value = parse_dose_value(str(product["dose"]))
                amount_pln = round(dose_value * max(product["area_ha"], 0.0) * max(product["price_per_unit"], 0.0), 2)
                conn.execute(
                    """
                    INSERT INTO costs (owner_username, treatment_id, cost_type, amount_pln, supplier, invoice_no, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner,
                        treatment_id,
                        f"{product['category']} / {product['product_name']}",
                        amount_pln,
                        "",
                        "",
                        f"Dawka: {product['dose']}; powierzchnia: {product['area_ha']} ha",
                    ),
                )
        conn.commit()
        if hasattr(conn, "sync"):
            conn.sync()
    _clear_data_cache()
    return inserted_treatments


def save_treatment(
    field_id: int,
    treatment_date: str,
    treatment_type: str,
    products: list[dict],
    notes: str,
    crop_id: Optional[int] = None,
    crop_name: str = "",
) -> int:
    return save_treatments([field_id], treatment_date, treatment_type, products, notes, crop_id, crop_name)


def parse_treatment_products(notes: Optional[str], row: dict) -> list[dict]:
    parsed_from_json = parse_products_payload(row.get("products_json"))
    if parsed_from_json:
        return parsed_from_json

    if notes:
        try:
            parts = [part.strip() for part in str(notes).splitlines() if part.strip()]
            if parts:
                parsed = json.loads(parts[-1])
                if isinstance(parsed, list):
                    return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    return [{
        "category": row.get("product_category") or PRODUCT_TABLES[0],
        "product_name": row.get("product_name") or row.get("product") or "",
        "price_per_unit": float(row.get("product_price") or 0.0),
        "unit": row.get("product_unit") or "",
        "dose": row.get("dose") or 0.0,
        "area_ha": row.get("area_ha") or 0.0,
    }]


def delete_treatment(treatment_id: int) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        conn.execute("DELETE FROM costs WHERE treatment_id = ? AND owner_username = ?", (treatment_id, owner))
        conn.execute("DELETE FROM treatments WHERE id = ? AND owner_username = ?", (treatment_id, owner))
        conn.commit()
    _clear_data_cache()


def delete_treatment_batch(batch_id: str, treatment_ids: list[int]) -> None:
    owner = _current_owner()
    with get_connection() as conn:
        if batch_id:
            treatment_rows = conn.execute(
                "SELECT id FROM treatments WHERE batch_id = ? AND owner_username = ?",
                (batch_id, owner),
            ).fetchall()
            resolved_treatment_ids = [int(row[0]) for row in treatment_rows]
        else:
            resolved_treatment_ids = [int(treatment_id) for treatment_id in treatment_ids]

        for treatment_id in resolved_treatment_ids:
            conn.execute("DELETE FROM costs WHERE treatment_id = ? AND owner_username = ?", (treatment_id, owner))
            conn.execute("DELETE FROM treatments WHERE id = ? AND owner_username = ?", (treatment_id, owner))
        conn.commit()
    _clear_data_cache()


def replace_treatment_batch(
    batch_id: str,
    previous_treatment_ids: list[int],
    field_ids: list[int],
    treatment_date: str,
    treatment_type: str,
    products: list[dict],
    notes: str,
) -> int:
    valid_field_ids = [int(field_id) for field_id in field_ids if field_id is not None]
    valid_products = [product for product in products if product.get("product_name")]
    if not valid_field_ids or not valid_products:
        return 0

    owner = _current_owner()
    field_areas = {
        field_id: float(get_field_plot_area(field_id))
        for field_id in valid_field_ids
    }
    total_selected_area = float(sum(field_areas.values()))
    equal_share = 1.0 / len(valid_field_ids)
    replacement_batch_id = str(batch_id or uuid4())

    with get_connection() as conn:
        try:
            for treatment_id in previous_treatment_ids:
                conn.execute(
                    "DELETE FROM costs WHERE treatment_id = ? AND owner_username = ?",
                    (int(treatment_id), owner),
                )
                conn.execute(
                    "DELETE FROM treatments WHERE id = ? AND owner_username = ?",
                    (int(treatment_id), owner),
                )

            for field_id in valid_field_ids:
                share_factor = (
                    field_areas[field_id] / total_selected_area
                    if total_selected_area > 0
                    else equal_share
                )
                field_products = [
                    {
                        **product,
                        "area_ha": round(
                            max(float(product.get("area_ha") or 0.0), 0.0) * share_factor,
                            4,
                        ),
                    }
                    for product in valid_products
                ]
                primary_product = field_products[0]
                resolved_crop_id, resolved_crop_name = resolve_treatment_crop(
                    field_id, treatment_type
                )
                cursor = conn.execute(
                    """
                    INSERT INTO treatments (
                        owner_username, batch_id, field_id, treatment_date, treatment_type, product, product_category, product_name, product_unit, product_price, dose, area_ha, crop_id, crop_name, notes, products_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner,
                        replacement_batch_id,
                        field_id,
                        treatment_date,
                        treatment_type,
                        primary_product["product_name"],
                        primary_product["category"],
                        primary_product["product_name"],
                        primary_product["unit"],
                        primary_product["price_per_unit"],
                        str(primary_product["dose"]),
                        primary_product["area_ha"],
                        resolved_crop_id,
                        resolved_crop_name,
                        str(notes or ""),
                        json.dumps(field_products, ensure_ascii=False),
                    ),
                )
                treatment_id = cursor.lastrowid
                for product in field_products:
                    dose_value = parse_dose_value(str(product["dose"]))
                    amount_pln = round(
                        dose_value * max(product["area_ha"], 0.0) * max(product["price_per_unit"], 0.0),
                        2,
                    )
                    conn.execute(
                        """
                        INSERT INTO costs (owner_username, treatment_id, cost_type, amount_pln, supplier, invoice_no, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            owner,
                            treatment_id,
                            f"{product['category']} / {product['product_name']}",
                            amount_pln,
                            "",
                            "",
                            f"Dawka: {product['dose']}; powierzchnia: {product['area_ha']} ha",
                        ),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    _clear_data_cache()
    return len(valid_field_ids)


def update_treatment(
    treatment_id: int,
    field_id: int,
    treatment_date: str,
    treatment_type: str,
    products: list[dict],
    notes: str,
    crop_id: Optional[int] = None,
    crop_name: str = "",
) -> None:
    if not products:
        return

    owner = _current_owner()
    resolved_crop_id, resolved_crop_name = resolve_treatment_crop(field_id, treatment_type, crop_id, crop_name)
    primary_product = products[0]
    primary_dose = str(primary_product["dose"])

    with get_connection() as conn:
        conn.execute("DELETE FROM costs WHERE treatment_id = ? AND owner_username = ?", (treatment_id, owner))
        conn.execute(
            """
            UPDATE treatments
            SET field_id = ?, treatment_date = ?, treatment_type = ?, product = ?, product_category = ?, product_name = ?, product_unit = ?, product_price = ?, dose = ?, area_ha = ?, crop_id = ?, crop_name = ?, notes = ?, products_json = ?
            WHERE id = ? AND owner_username = ?
            """,
            (
                field_id,
                treatment_date,
                treatment_type,
                primary_product["product_name"],
                primary_product["category"],
                primary_product["product_name"],
                primary_product["unit"],
                primary_product["price_per_unit"],
                primary_dose,
                primary_product["area_ha"],
                resolved_crop_id,
                resolved_crop_name,
                str(notes or ""),
                json.dumps(products, ensure_ascii=False),
                treatment_id,
                owner,
            ),
        )

        for product in products:
            dose_value = parse_dose_value(str(product["dose"]))
            amount_pln = round(dose_value * max(product["area_ha"], 0.0) * max(product["price_per_unit"], 0.0), 2)
            conn.execute(
                """
                INSERT INTO costs (owner_username, treatment_id, cost_type, amount_pln, supplier, invoice_no, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner,
                    treatment_id,
                    f"{product['category']} / {product['product_name']}",
                    amount_pln,
                    "",
                    "",
                    f"Dawka: {product['dose']}; powierzchnia: {product['area_ha']} ha",
                ),
            )
        conn.commit()
    _clear_data_cache()


@st.cache_resource
def _init_db_once() -> None:
    init_db()


def main() -> None:
    st.set_page_config(page_title="Farmenager", page_icon="🌾", layout="wide")
    require_authentication()
    show_password_change_form()
    _init_db_once()

    st.title("System ewidencji zabiegów agrotechnicznych")
    st.caption("Zarządzanie polami, gospodarstwami, działkami, zabiegami i kosztami")

    _owner = _current_owner()
    fields_df = load_fields(_owner)
    farms_df = load_farms(_owner)
    seasons_df = load_seasons(_owner)
    crops_df = load_crops(_owner)
    crop_assignments_df = load_crop_assignments(_owner)
    plots_df = load_plots(_owner)
    treatments_df = load_treatments(_owner)
    costs_df = load_costs(_owner)

    product_catalogs = {table_name: load_product_catalog(table_name, _owner) for table_name in PRODUCT_TABLES}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Liczba pól", len(fields_df))
    col2.metric("Liczba gospodarstw", len(farms_df))
    col3.metric("Liczba działek", len(plots_df))
    col4.metric("Suma kosztów", f"{costs_df['amount_pln'].sum():,.2f} zł" if not costs_df.empty else "0,00 zł")

    tab1, tab2, tab3 = st.tabs(["Gospodarstwo", "Zarządzanie", "Raporty"])

    with tab1:
        st.subheader("Gospodarstwo")
        st.caption("Wybierz funkcję, aby przejść do odpowiednich formularzy i list.")

        if "farm_section" not in st.session_state:
            st.session_state.farm_section = "pole"

        button_cols = st.columns(5)
        with button_cols[0]:
            if st.button("Dodaj pole", key="farm_section_pole", use_container_width=True):
                st.session_state.farm_section = "pole"
        with button_cols[1]:
            if st.button("Dodaj działkę", key="farm_section_plot", use_container_width=True):
                st.session_state.farm_section = "plot"
        with button_cols[2]:
            if st.button("Dodaj gospodarstwo", key="farm_section_farm", use_container_width=True):
                st.session_state.farm_section = "farm"
        with button_cols[3]:
            if st.button("Dodaj sezon wegetacyjny", key="farm_section_season", use_container_width=True):
                st.session_state.farm_section = "season"
        with button_cols[4]:
            if st.button("Dodaj uprawę", key="farm_section_crop", use_container_width=True):
                st.session_state.farm_section = "crop"

        st.divider()

        if st.session_state.farm_section == "pole":
            st.subheader("Dodaj pole")
            with st.form("form_field", clear_on_submit=True):
                name = st.text_input("Nazwa pola")
                st.write("Powierzchnia pola jest obliczana jako suma powierzchni działek przypisanych do tego pola.")
                st.write("Powierzchnia [ha]: 0.0")
                notes = st.text_area("Notatki")
                submitted = st.form_submit_button("Zapisz pole")
                if submitted:
                    if name:
                        save_field(name, 0.0, notes)
                        st.success("Pole zapisane")
                        st.rerun()
                    else:
                        st.warning("Podaj nazwę pola")

            st.subheader("Lista pól")
            if not fields_df.empty:
                header_cols = st.columns([3.2, 1.6, 1.2])
                header_cols[0].markdown("**Nazwa pola**")
                header_cols[1].markdown("**Powierzchnia [ha]**")
                header_cols[2].markdown("**Usuń**")

                for _, row in fields_df.iterrows():
                    field_id = int(row["id"])
                    field_area = get_field_plot_area(field_id)
                    row_cols = st.columns([3.2, 1.6, 1.2])
                    edited_name = row_cols[0].text_input(
                        "Nazwa pola",
                        value=str(row["name"]),
                        key=f"field_list_name_{field_id}",
                        label_visibility="collapsed",
                    )
                    row_cols[1].write(f"{field_area:.2f}")
                    delete_action = f"field_{field_id}"
                    if row_cols[2].button("Usuń", key=f"delete_field_button_{field_id}", use_container_width=True):
                        st.session_state.pending_delete_action = delete_action
                    if _delete_confirmation(delete_action, f"pole „{row['name']}”"):
                        delete_field(field_id)
                        st.success("Pole usunięte")
                        st.rerun()

                    if edited_name != str(row["name"]):
                        st.session_state[f"field_list_changed_{field_id}"] = True

                if st.button("Zapisz zmiany nazw pól", key="save_field_list_changes"):
                    for _, row in fields_df.iterrows():
                        field_id = int(row["id"])
                        updated_name = str(st.session_state.get(f"field_list_name_{field_id}", row["name"]))
                        if updated_name != str(row["name"]):
                            update_field(field_id, updated_name, 0.0, str(row["notes"] or ""))
                    st.success("Lista pól zaktualizowana")
                    st.rerun()
            else:
                st.info("Brak pól do wyświetlenia")

        elif st.session_state.farm_section == "plot":
            st.subheader("Dodaj działkę")
            with st.form("form_plot", clear_on_submit=True):
                farm_options = {row["name"]: row["id"] for _, row in farms_df.iterrows()}
                if farm_options:
                    selected_farm_name = st.selectbox("Gospodarstwo", options=list(farm_options.keys()))
                    farm_id = farm_options[selected_farm_name]
                else:
                    st.info("Najpierw dodaj gospodarstwo")
                    farm_id = None

                field_options = {row["name"]: row["id"] for _, row in fields_df.iterrows()}
                if field_options:
                    selected_field_name = st.selectbox("Pole", options=["Brak przypisania", *list(field_options.keys())])
                    field_id = None if selected_field_name == "Brak przypisania" else field_options[selected_field_name]
                else:
                    field_id = None

                plot_name = st.text_input("Nazwa działki")
                area_ha = st.number_input("Powierzchnia działki [ha]", min_value=0.0, step=0.1)
                plot_notes = st.text_area("Notatki")
                submitted = st.form_submit_button("Zapisz działkę")
                if submitted:
                    if farm_id and plot_name:
                        save_plot(farm_id=farm_id, field_id=field_id, name=plot_name, area_ha=area_ha, notes=plot_notes)
                        st.success("Działka zapisana")
                        st.rerun()
                    else:
                        st.warning("Wybierz gospodarstwo i podaj nazwę działki")

            st.subheader("Lista działek")
            if not plots_df.empty:
                farm_select_options = []
                farm_id_lookup = {}
                for _, farm_row in farms_df.iterrows():
                    farm_label = f"{str(farm_row['name'])} [ID {int(farm_row['id'])}]"
                    farm_select_options.append(farm_label)
                    farm_id_lookup[farm_label] = int(farm_row["id"])

                field_select_options = ["Brak przypisania"]
                field_id_lookup = {"Brak przypisania": None}
                for _, field_row in fields_df.iterrows():
                    field_label = f"{str(field_row['name'])} [ID {int(field_row['id'])}]"
                    field_select_options.append(field_label)
                    field_id_lookup[field_label] = int(field_row["id"])

                header_cols = st.columns([2.4, 1.2, 2.2, 2.2, 1.0])
                header_cols[0].markdown("**Nazwa działki**")
                header_cols[1].markdown("**Powierzchnia [ha]**")
                header_cols[2].markdown("**Pole**")
                header_cols[3].markdown("**Gospodarstwo**")
                header_cols[4].markdown("**Usuń**")

                for _, row in plots_df.iterrows():
                    plot_id = int(row["id"])
                    current_farm_label = next(
                        (label for label, farm_id in farm_id_lookup.items() if farm_id == int(row["farm_id"])),
                        farm_select_options[0] if farm_select_options else "",
                    )
                    current_field_id = int(row["field_id"]) if pd.notna(row["field_id"]) else None
                    current_field_label = next(
                        (label for label, field_id in field_id_lookup.items() if field_id == current_field_id),
                        "Brak przypisania",
                    )

                    row_cols = st.columns([2.4, 1.2, 2.2, 2.2, 1.0])
                    row_cols[0].text_input(
                        "Nazwa działki",
                        value=str(row["name"]),
                        key=f"plot_list_name_{plot_id}",
                        label_visibility="collapsed",
                    )
                    row_cols[1].number_input(
                        "Powierzchnia działki [ha]",
                        min_value=0.0,
                        step=0.1,
                        value=float(row["area_ha"]),
                        key=f"plot_list_area_{plot_id}",
                        label_visibility="collapsed",
                    )
                    row_cols[2].selectbox(
                        "Pole",
                        options=field_select_options,
                        index=field_select_options.index(current_field_label) if current_field_label in field_select_options else 0,
                        key=f"plot_list_field_{plot_id}",
                        label_visibility="collapsed",
                    )
                    row_cols[3].selectbox(
                        "Gospodarstwo",
                        options=farm_select_options,
                        index=farm_select_options.index(current_farm_label) if current_farm_label in farm_select_options else 0,
                        key=f"plot_list_farm_{plot_id}",
                        label_visibility="collapsed",
                    )
                    delete_action = f"plot_{plot_id}"
                    if row_cols[4].button("Usuń", key=f"delete_plot_button_{plot_id}", use_container_width=True):
                        st.session_state.pending_delete_action = delete_action
                    if _delete_confirmation(delete_action, f"działkę „{row['name']}”"):
                        delete_plot(plot_id)
                        st.success("Działka usunięta")
                        st.rerun()

                if st.button("Zapisz zmiany działek", key="save_plot_list_changes"):
                    for _, row in plots_df.iterrows():
                        plot_id = int(row["id"])
                        updated_name = str(st.session_state.get(f"plot_list_name_{plot_id}", row["name"]))
                        updated_area = float(st.session_state.get(f"plot_list_area_{plot_id}", row["area_ha"]))
                        selected_field_label = st.session_state.get(f"plot_list_field_{plot_id}", "Brak przypisania")
                        selected_farm_label = st.session_state.get(f"plot_list_farm_{plot_id}")
                        updated_field_id = field_id_lookup.get(selected_field_label)
                        updated_farm_id = farm_id_lookup.get(selected_farm_label, int(row["farm_id"]))
                        update_plot(
                            plot_id,
                            int(updated_farm_id),
                            updated_field_id,
                            updated_name,
                            updated_area,
                            str(row["notes"] or ""),
                        )
                    st.success("Lista działek zaktualizowana")
                    st.rerun()
            else:
                st.info("Brak działek do wyświetlenia")

        elif st.session_state.farm_section == "farm":
            st.subheader("Dodaj gospodarstwo")
            with st.form("form_farm", clear_on_submit=True):
                farm_name = st.text_input("Nazwa gospodarstwa")
                farm_notes = st.text_area("Notatki")
                submitted = st.form_submit_button("Zapisz gospodarstwo")
                if submitted:
                    if farm_name:
                        save_farm(farm_name, farm_notes)
                        st.success("Gospodarstwo zapisane")
                        st.rerun()
                    else:
                        st.warning("Podaj nazwę gospodarstwa")

            st.subheader("Lista gospodarstw")
            if not farms_df.empty:
                header_cols = st.columns([4, 1.2])
                header_cols[0].markdown("**Nazwa gospodarstwa**")
                header_cols[1].markdown("**Usuń**")

                for _, row in farms_df.iterrows():
                    farm_id = int(row["id"])
                    row_cols = st.columns([4, 1.2])
                    row_cols[0].text_input(
                        "Nazwa gospodarstwa",
                        value=str(row["name"]),
                        key=f"farm_list_name_{farm_id}",
                        label_visibility="collapsed",
                    )
                    delete_action = f"farm_{farm_id}"
                    if row_cols[1].button("Usuń", key=f"delete_farm_button_{farm_id}", use_container_width=True):
                        st.session_state.pending_delete_action = delete_action
                    if _delete_confirmation(delete_action, f"gospodarstwo „{row['name']}”"):
                        delete_farm(farm_id)
                        st.success("Gospodarstwo usunięte")
                        st.rerun()

                if st.button("Zapisz zmiany gospodarstw", key="save_farm_list_changes"):
                    for _, row in farms_df.iterrows():
                        farm_id = int(row["id"])
                        updated_name = str(st.session_state.get(f"farm_list_name_{farm_id}", row["name"]))
                        if updated_name != str(row["name"]):
                            update_farm(farm_id, updated_name, str(row["notes"] or ""))
                    st.success("Lista gospodarstw zaktualizowana")
                    st.rerun()
            else:
                st.info("Brak gospodarstw do wyświetlenia")

            if not plots_df.empty:
                summary_df = (
                    plots_df.groupby("farm_name", dropna=False)["area_ha"]
                    .sum()
                    .reset_index()
                    .rename(columns={"area_ha": "suma_powierzchni_ha", "farm_name": "gospodarstwo"})
                )
                st.subheader("Podsumowanie powierzchni po gospodarstwie")
                st.dataframe(summary_df, use_container_width=True, hide_index=True)

        elif st.session_state.farm_section == "season":
            st.subheader("Dodaj sezon wegetacyjny")
            with st.form("form_season", clear_on_submit=True):
                season_name = st.text_input("Nazwa sezonu")
                season_notes = st.text_area("Notatki")
                submitted = st.form_submit_button("Zapisz sezon")
                if submitted:
                    if season_name:
                        save_season(season_name, season_notes)
                        st.success("Sezon zapisany")
                        st.rerun()
                    else:
                        st.warning("Podaj nazwę sezonu")

            st.subheader("Lista sezonów wegetacyjnych")
            if not seasons_df.empty:
                header_cols = st.columns([3.3, 1.1, 1.2])
                header_cols[0].markdown("**Nazwa sezonu**")
                header_cols[1].markdown("**Domyślny**")
                header_cols[2].markdown("**Usuń**")

                selected_default_ids = []

                for _, row in seasons_df.iterrows():
                    season_id = int(row["id"])
                    row_cols = st.columns([3.3, 1.1, 1.2])
                    row_cols[0].text_input(
                        "Nazwa sezonu",
                        value=str(row["name"]),
                        key=f"season_list_name_{season_id}",
                        label_visibility="collapsed",
                    )
                    default_checkbox_key = f"season_list_default_{season_id}"
                    if default_checkbox_key not in st.session_state:
                        st.session_state[default_checkbox_key] = bool(int(row.get("is_default") or 0))

                    is_default_checked = row_cols[1].checkbox(
                        "Domyślny",
                        value=bool(st.session_state.get(default_checkbox_key, False)),
                        key=default_checkbox_key,
                        label_visibility="collapsed",
                    )
                    if is_default_checked:
                        selected_default_ids.append(season_id)

                    delete_action = f"season_{season_id}"
                    if row_cols[2].button("Usuń", key=f"delete_season_button_{season_id}", use_container_width=True):
                        st.session_state.pending_delete_action = delete_action
                    if _delete_confirmation(delete_action, f"sezon „{row['name']}”"):
                        delete_season(season_id)
                        st.success("Sezon usunięty")
                        st.rerun()

                if len(selected_default_ids) > 1:
                    kept_default_id = selected_default_ids[-1]
                    for _, row in seasons_df.iterrows():
                        season_id = int(row["id"])
                        st.session_state[f"season_list_default_{season_id}"] = season_id == kept_default_id
                    st.rerun()

                if st.button("Zapisz zmiany sezonów", key="save_season_list_changes"):
                    for _, row in seasons_df.iterrows():
                        season_id = int(row["id"])
                        updated_name = str(st.session_state.get(f"season_list_name_{season_id}", row["name"]))
                        if updated_name != str(row["name"]):
                            update_season(season_id, updated_name, str(row["notes"] or ""))

                    selected_default_id = None
                    for _, row in seasons_df.iterrows():
                        season_id = int(row["id"])
                        if bool(st.session_state.get(f"season_list_default_{season_id}", False)):
                            selected_default_id = season_id
                            break
                    set_default_season(selected_default_id)
                    st.success("Lista sezonów zaktualizowana")
                    st.rerun()
            else:
                st.info("Brak sezonów do wyświetlenia")

        elif st.session_state.farm_section == "crop":
            st.subheader("Dodaj uprawę")
            with st.form("form_crop", clear_on_submit=True):
                crop_name = st.text_input("Nazwa uprawy")
                crop_notes = st.text_area("Notatki")
                submitted = st.form_submit_button("Zapisz uprawę")
                if submitted:
                    if crop_name:
                        save_crop(crop_name, crop_notes)
                        st.success("Uprawa zapisana")
                        st.rerun()
                    else:
                        st.warning("Podaj nazwę uprawy")

            st.subheader("Lista upraw")
            crops_df = load_crops(_current_owner())
            if crops_df.empty:
                st.info("Brak upraw do wyświetlenia")
            else:
                header_cols = st.columns([4, 1.2])
                header_cols[0].markdown("**Nazwa uprawy**")
                header_cols[1].markdown("**Usuń**")

                for _, row in crops_df.iterrows():
                    crop_id = int(row["id"])
                    row_cols = st.columns([4, 1.2])
                    row_cols[0].text_input(
                        "Nazwa uprawy",
                        value=str(row["name"]),
                        key=f"crop_list_name_{crop_id}",
                        label_visibility="collapsed",
                    )
                    delete_action = f"crop_{crop_id}"
                    if row_cols[1].button("Usuń", key=f"delete_crop_button_{crop_id}", use_container_width=True):
                        st.session_state.pending_delete_action = delete_action
                    if _delete_confirmation(delete_action, f"uprawę „{row['name']}”"):
                        delete_crop(crop_id)
                        st.success("Uprawa usunięta")
                        st.rerun()

                if st.button("Zapisz zmiany upraw", key="save_crop_list_changes"):
                    for _, row in crops_df.iterrows():
                        crop_id = int(row["id"])
                        updated_name = str(st.session_state.get(f"crop_list_name_{crop_id}", row["name"]))
                        if updated_name != str(row["name"]):
                            update_crop(crop_id, updated_name, str(row["notes"] or ""))
                    st.success("Lista upraw zaktualizowana")
                    st.rerun()

    with tab2:
        st.subheader("Zarządzanie")
        st.caption("Wybierz funkcję, aby przejść do odpowiednich formularzy i list.")

        if "management_section" not in st.session_state:
            st.session_state.management_section = "treatments_list"

        is_admin = is_admin_username(str(st.session_state.get(SESSION_AUTH_USERNAME) or ""))
        button_cols = st.columns(5 if is_admin else 4)
        with button_cols[0]:
            if st.button("Lista zabiegów", key="management_treatments_list", use_container_width=True):
                st.session_state.management_section = "treatments_list"
        with button_cols[1]:
            if st.button("Dodaj zabieg", key="management_treatments_add", use_container_width=True):
                st.session_state.management_section = "treatments_add"
        with button_cols[2]:
            if st.button("Produkty", key="management_products", use_container_width=True):
                st.session_state.management_section = "products"
        with button_cols[3]:
            if st.button("Koszty", key="management_costs", use_container_width=True):
                st.session_state.management_section = "costs"
        if is_admin:
            with button_cols[4]:
                if st.button("Użytkownicy", key="management_users", use_container_width=True):
                    st.session_state.management_section = "users"

        st.divider()

        if st.session_state.management_section == "treatments_list":
            st.subheader("Lista zabiegów")
            treatment_groups = build_treatment_list_groups(treatments_df)

            @st.dialog("Edytuj zabieg", width="large")
            def open_treatment_batch_editor(group: dict) -> None:
                group_key = str(group["group_key"])
                current_field_ids = [int(row["field_id"]) for row in group["fields"]]
                field_options = {
                    int(row["id"]): str(row["name"])
                    for _, row in fields_df.iterrows()
                }
                field_area_lookup = {
                    int(field_id): get_field_plot_area(int(field_id))
                    for field_id in field_options
                }
                selected_field_ids = st.multiselect(
                    "Pola",
                    options=list(field_options.keys()),
                    default=current_field_ids,
                    format_func=lambda field_id: field_options[field_id],
                    key=f"batch_edit_fields_{group_key}",
                )
                edit_date = st.date_input(
                    "Data zabiegu",
                    value=date.fromisoformat(group["treatment_date"]),
                    key=f"batch_edit_date_{group_key}",
                )
                season_names = list(seasons_df["name"]) if not seasons_df.empty else []
                if season_names:
                    selected_season = st.selectbox(
                        "Sezon",
                        options=season_names,
                        index=season_names.index(group["season"]) if group["season"] in season_names else 0,
                        key=f"batch_edit_season_{group_key}",
                    )
                else:
                    st.warning("Najpierw dodaj sezon wegetacyjny")
                    selected_season = ""

                total_area = sum(field_area_lookup.get(int(field_id), 0.0) for field_id in selected_field_ids)
                product_state_key = f"batch_edit_product_rows_{group_key}"
                if product_state_key not in st.session_state:
                    st.session_state[product_state_key] = [
                        {**product, "area_ha": total_area}
                        for product in group["products"]
                    ]

                st.markdown("**Produkty**")
                product_rows = st.session_state[product_state_key]
                for product_index, product_row in enumerate(product_rows):
                    product_cols = st.columns([2, 2, 1.3, 1.2, 0.7])
                    current_category = str(product_row.get("category") or PRODUCT_TABLES[0])
                    category_index = PRODUCT_TABLES.index(current_category) if current_category in PRODUCT_TABLES else 0
                    category = product_cols[0].selectbox(
                        "Kategoria",
                        options=PRODUCT_TABLES,
                        index=category_index,
                        key=f"batch_edit_category_{group_key}_{product_index}",
                    )
                    product_catalog = load_product_catalog(category, _current_owner())
                    if not product_catalog.empty:
                        product_names = list(product_catalog["name"])
                        current_product_name = str(product_row.get("product_name") or "")
                        product_index_in_catalog = product_names.index(current_product_name) if current_product_name in product_names else 0
                        selected_product_name = product_cols[1].selectbox(
                            "Produkt",
                            options=product_names,
                            index=product_index_in_catalog,
                            key=f"batch_edit_product_{group_key}_{product_index}",
                        )
                        selected_product = product_catalog[product_catalog["name"] == selected_product_name].iloc[0]
                        product_row["category"] = category
                        product_row["product_name"] = selected_product_name
                        product_row["price_per_unit"] = float(selected_product["price_per_unit"])
                        product_row["unit"] = str(selected_product["unit"])
                    else:
                        product_cols[1].info("Brak produktów")
                        product_row["category"] = category
                        product_row["product_name"] = ""
                        product_row["price_per_unit"] = 0.0
                        product_row["unit"] = ""

                    product_row["dose"] = product_cols[2].number_input(
                        "Dawka na ha",
                        min_value=0.0,
                        step=0.1,
                        value=float(product_row.get("dose") or 0.0),
                        key=f"batch_edit_dose_{group_key}_{product_index}",
                    )
                    product_cols[3].write(f"{total_area:.2f} ha")
                    product_row["area_ha"] = total_area
                    if product_cols[4].button("Usuń", key=f"remove_batch_product_{group_key}_{product_index}"):
                        product_rows.pop(product_index)
                        st.rerun(scope="fragment")

                if st.button("Dodaj produkt", key=f"add_batch_product_{group_key}"):
                    product_rows.append(
                        {
                            "category": PRODUCT_TABLES[0],
                            "product_name": "",
                            "price_per_unit": 0.0,
                            "unit": "",
                            "dose": 0.0,
                            "area_ha": total_area,
                        }
                    )
                    st.rerun(scope="fragment")
                edit_notes = st.text_area(
                    "Opis",
                    value=extract_user_notes(group["notes"]),
                    key=f"batch_edit_notes_{group_key}",
                )

                action_cols = st.columns(2)
                with action_cols[0]:
                    if st.button("Zapisz zmiany", key=f"save_batch_edit_{group_key}", use_container_width=True):
                        products = [
                            product_row
                            for product_row in product_rows
                            if product_row.get("product_name")
                        ]
                        if selected_field_ids and selected_season and products:
                            inserted_count = replace_treatment_batch(
                                batch_id=group["batch_id"],
                                previous_treatment_ids=group["treatment_ids"],
                                field_ids=selected_field_ids,
                                treatment_date=edit_date.strftime("%Y-%m-%d"),
                                treatment_type=selected_season,
                                products=products,
                                notes=edit_notes,
                            )
                            if inserted_count:
                                del st.session_state[product_state_key]
                                st.rerun(scope="app")
                            else:
                                st.error("Nie udało się zaktualizować zabiegu")
                        else:
                            st.warning("Wybierz pola, sezon i zachowaj co najmniej jeden produkt")
                with action_cols[1]:
                    delete_action = f"treatment_batch_{group_key}"
                    if st.button("Usuń zabieg", key=f"delete_batch_{group_key}", use_container_width=True):
                        st.session_state.pending_delete_action = delete_action
                    if _delete_confirmation(delete_action, f"zabieg z dnia {group['treatment_date']} ({group['field_name']})"):
                        delete_treatment_batch(group["batch_id"], group["treatment_ids"])
                        st.session_state.pop(product_state_key, None)
                        st.rerun(scope="app")

            if not treatment_groups:
                st.info("Brak zabiegów do wyświetlenia")
            else:
                header_cols = st.columns([1.3, 1.4, 3.2, 1.5, 3.2, 1])
                for column, label in zip(header_cols, ["**Data**", "**Sezon**", "**Pola**", "**Suma powierzchni [ha]**", "**Produkty**", ""]):
                    column.markdown(label)
                st.divider()

                for group in treatment_groups:
                    group_key = str(group["group_key"])
                    row_cols = st.columns([1.3, 1.4, 3.2, 1.5, 3.2, 1])
                    row_cols[0].write(group["treatment_date"])
                    row_cols[1].write(group["season"])
                    row_cols[2].write(group["field_name"])
                    row_cols[3].write(f"{float(group['total_area_ha']):.2f}")
                    row_cols[4].write(group["product_name"])
                    if row_cols[5].button("Edytuj", key=f"open_batch_edit_{group_key}", use_container_width=True):
                        st.session_state.pop(f"batch_edit_product_rows_{group_key}", None)
                        open_treatment_batch_editor(group)
                    st.divider()

        elif st.session_state.management_section == "treatments_list_legacy":
            if not treatments_df.empty:
                treatment_options = {
                    f"{row['treatment_date']} | {row['field_name'] or '-'} | {row['season'] or '-'} | {row['product_name'] or row['product'] or '-'}": int(row["id"])
                    for _, row in treatments_df.iterrows()
                }
                selected_treatment_label = st.selectbox("Wybierz zabieg", options=list(treatment_options.keys()), key="edit_treatment_select")
                selected_treatment_id = treatment_options[selected_treatment_label]
                selected_treatment = treatments_df[treatments_df["id"] == selected_treatment_id].iloc[0]

                delete_action = f"legacy_treatment_{selected_treatment_id}"
                if st.button("Usuń wybrany zabieg", key="delete_selected_treatment"):
                    st.session_state.pending_delete_action = delete_action
                if _delete_confirmation(
                    delete_action,
                    f"zabieg z dnia {selected_treatment['treatment_date']} ({selected_treatment['field_name']})",
                ):
                    delete_treatment(selected_treatment_id)
                    st.success("Zabieg usunięty")
                    st.rerun()

                if st.button("Wczytaj zabieg do edycji", key="load_treatment_for_edit"):
                    st.session_state["edit_treatment_id"] = int(selected_treatment_id)
                    st.session_state["edit_treatment_products"] = parse_treatment_products(selected_treatment.get("notes"), selected_treatment)
                    st.session_state["edit_treatment_selected_fields"] = [int(selected_treatment.get("field_id"))] if pd.notna(selected_treatment.get("field_id")) else []
                    st.session_state["edit_treatment_last_selected_fields"] = list(st.session_state.get("edit_treatment_selected_fields", []))
                    st.session_state["edit_treatment_field_id"] = int(selected_treatment.get("field_id")) if pd.notna(selected_treatment.get("field_id")) else None
                    st.session_state["edit_treatment_date"] = selected_treatment.get("treatment_date")
                    st.session_state["edit_treatment_type"] = selected_treatment.get("season")
                    st.session_state["edit_treatment_crop_id"] = int(selected_treatment.get("crop_id")) if pd.notna(selected_treatment.get("crop_id")) else None
                    st.session_state["edit_treatment_crop_name"] = selected_treatment.get("crop_name") or ""
                    st.session_state["edit_treatment_notes"] = extract_user_notes(selected_treatment.get("notes"))
                    st.rerun()

                if st.session_state.get("edit_treatment_id") == int(selected_treatment_id):
                    st.markdown("### Edytuj wybrany zabieg")

                    if "edit_treatment_selected_fields" not in st.session_state:
                        st.session_state.edit_treatment_selected_fields = []
                    if "edit_treatment_products" not in st.session_state:
                        st.session_state.edit_treatment_products = []
                    if "edit_treatment_last_selected_fields" not in st.session_state:
                        st.session_state.edit_treatment_last_selected_fields = []

                    edit_date = st.date_input("Data zabiegu", value=date.fromisoformat(st.session_state.get("edit_treatment_date") or date.today().isoformat()), key="edit_treatment_date_input")

                    if not seasons_df.empty:
                        season_options = {row["name"]: int(row["id"]) for _, row in seasons_df.iterrows()}
                        season_names = list(season_options.keys())
                        current_edit_season_name = st.session_state.get("edit_treatment_type") or (season_names[0] if season_names else "")
                        edit_season_name = st.selectbox(
                            "Sezon",
                            options=season_names,
                            index=season_names.index(current_edit_season_name) if current_edit_season_name in season_names else 0,
                            key="edit_treatment_season",
                        )
                        edit_season_id = season_options[edit_season_name]
                        edit_treatment_type = edit_season_name
                    else:
                        st.info("Najpierw dodaj sezon wegetacyjny")
                        edit_season_id = None
                        edit_treatment_type = ""

                    edit_field_options = {str(row["name"]): int(row["id"]) for _, row in fields_df.iterrows()}
                    available_edit_field_names = [name for name in edit_field_options.keys() if edit_field_options[name] not in st.session_state.edit_treatment_selected_fields]
                    if edit_field_options:
                        selected_edit_field_name = st.selectbox("Pole", options=available_edit_field_names, key="edit_treatment_field") if available_edit_field_names else ""
                        if st.button("Dodaj kolejne pole", key="add_edit_treatment_field"):
                            if selected_edit_field_name:
                                selected_field_id = edit_field_options[selected_edit_field_name]
                                if selected_field_id not in st.session_state.edit_treatment_selected_fields:
                                    st.session_state.edit_treatment_selected_fields.append(selected_field_id)
                                st.rerun()
                    else:
                        st.info("Najpierw dodaj pole")

                    if st.session_state.edit_treatment_selected_fields:
                        selected_field_rows = []
                        for field_id in st.session_state.edit_treatment_selected_fields:
                            field_row = fields_df[fields_df["id"] == int(field_id)]
                            if not field_row.empty:
                                selected_field_rows.append(
                                    {
                                        "field_id": int(field_id),
                                        "pole": str(field_row.iloc[0]["name"]),
                                        "powierzchnia_ha": float(get_field_plot_area(int(field_id))),
                                    }
                                )

                        st.markdown("**Wybrane pola**")
                        selected_fields_df = pd.DataFrame(selected_field_rows)

                        header_cols = st.columns([3.2, 1.6, 0.8])
                        header_cols[0].markdown("**Pole**")
                        header_cols[1].markdown("**Powierzchnia [ha]**")
                        header_cols[2].markdown("")

                        for _, row in selected_fields_df.iterrows():
                            cols = st.columns([3.2, 1.6, 0.8])
                            cols[0].write(str(row["pole"]))
                            cols[1].write(f"{float(row['powierzchnia_ha']):.2f}")
                            if cols[2].button("✕", key=f"remove_edit_treatment_field_{int(row['field_id'])}", use_container_width=True):
                                st.session_state.edit_treatment_selected_fields.remove(int(row["field_id"]))
                                st.session_state.edit_treatment_last_selected_fields = list(st.session_state.edit_treatment_selected_fields)
                                st.rerun()

                        total_selected_area = float(selected_fields_df["powierzchnia_ha"].sum())
                        st.metric("Suma powierzchni", f"{total_selected_area:.2f} ha")
                    else:
                        st.info("Brak wybranych pól. Dodaj przynajmniej jedno pole.")

                    if st.session_state.edit_treatment_selected_fields:
                        total_selected_area = float(
                            sum(get_field_plot_area(int(field_id)) for field_id in st.session_state.edit_treatment_selected_fields)
                        )
                    else:
                        total_selected_area = 0.0

                    if st.session_state.edit_treatment_last_selected_fields != st.session_state.edit_treatment_selected_fields:
                        for product_row in st.session_state.edit_treatment_products:
                            product_row["area_ha"] = total_selected_area
                        st.session_state.edit_treatment_last_selected_fields = list(st.session_state.edit_treatment_selected_fields)

                    edit_crop_id = None
                    edit_crop_name = ""
                    if st.session_state.edit_treatment_selected_fields and edit_season_id is not None:
                        crop_names = []
                        crop_ids = []
                        for field_id in st.session_state.edit_treatment_selected_fields:
                            assignment = get_crop_assignment(int(field_id), edit_season_id)
                            if assignment:
                                crop_name = str(assignment.get("crop_name") or "")
                                crop_id = int(assignment["crop_id"]) if assignment.get("crop_id") is not None else None
                                crop_names.append(crop_name)
                                crop_ids.append(crop_id)
                            else:
                                crop_names.append("")
                                crop_ids.append(None)

                        unique_crop_names = [name for name in crop_names if name]
                        if unique_crop_names and len(set(unique_crop_names)) > 1:
                            crop_display_text = "Wiele upraw"
                        elif unique_crop_names:
                            crop_display_text = unique_crop_names[0]
                            edit_crop_id = crop_ids[0]
                            edit_crop_name = unique_crop_names[0]
                        else:
                            crop_display_text = "Nie wybrana uprawa. Uzupełnij w Raporty -> Płodozmian"

                        st.caption(f"Przypisana uprawa: {crop_display_text}")
                    elif st.session_state.edit_treatment_selected_fields:
                        st.caption("Wybierz sezon, aby zobaczyć przypisaną uprawę.")
                    else:
                        st.caption("Dodaj przynajmniej jedno pole, aby zobaczyć przypisaną uprawę.")

                    product_action_cols = st.columns([1, 1])
                    with product_action_cols[0]:
                        if st.button("Dodaj produkt", key="add_edit_treatment_product", use_container_width=True):
                            st.session_state.edit_treatment_products.append({
                                "category": PRODUCT_TABLES[0],
                                "product_name": "",
                                "price_per_unit": 0.0,
                                "unit": "",
                                "dose": 0.0,
                                "area_ha": total_selected_area,
                            })
                            st.rerun()
                    with product_action_cols[1]:
                        if st.button("Usuń", key="remove_last_edit_treatment_product", use_container_width=True, disabled=not st.session_state.edit_treatment_products):
                            if st.session_state.edit_treatment_products:
                                st.session_state.edit_treatment_products.pop()
                                st.rerun()

                    notes_value = st.text_area("Opis", value=str(st.session_state.get("edit_treatment_notes") or ""), key="edit_treatment_notes_input")

                    if st.session_state.edit_treatment_products:
                        for idx, product_row in enumerate(st.session_state.edit_treatment_products):
                            st.markdown(f"### Produkt {idx + 1}")
                            col1, col2, col3, col4 = st.columns([2, 2, 1.5, 1.5])
                            with col1:
                                current_category = product_row.get("category") or PRODUCT_TABLES[0]
                                category_index = PRODUCT_TABLES.index(current_category) if current_category in PRODUCT_TABLES else 0
                                category = st.selectbox("Kategoria", options=PRODUCT_TABLES, index=category_index, key=f"edit_treatment_category_{idx}")
                                if category != product_row.get("category"):
                                    product_row["category"] = category
                                    product_row["product_name"] = ""
                                    product_row["price_per_unit"] = 0.0
                                    product_row["unit"] = ""
                                else:
                                    product_row["category"] = category
                            with col2:
                                product_catalog = load_product_catalog(product_row["category"], _current_owner())
                                if not product_catalog.empty:
                                    product_names = list(product_catalog["name"])
                                    current_product = product_row.get("product_name") or ""
                                    product_index = product_names.index(current_product) if current_product in product_names else 0
                                    selected_product_name = st.selectbox("Produkt", options=product_names, index=product_index, key=f"edit_treatment_product_{idx}")
                                    selected_row = product_catalog[product_catalog["name"] == selected_product_name].iloc[0]
                                    product_row["product_name"] = selected_product_name
                                    product_row["price_per_unit"] = float(selected_row["price_per_unit"])
                                    product_row["unit"] = selected_row["unit"]
                                else:
                                    st.info("Brak produktów w tej kategorii")
                                    product_row["product_name"] = ""
                                    product_row["price_per_unit"] = 0.0
                                    product_row["unit"] = ""
                            with col3:
                                dose = st.number_input("Dawka na ha", min_value=0.0, step=0.1, value=float(product_row.get("dose") or 0.0), key=f"edit_treatment_dose_{idx}")
                                product_row["dose"] = dose
                            with col4:
                                area_ha = st.number_input("Powierzchnia zabiegu [ha]", min_value=0.0, step=0.1, value=float(product_row.get("area_ha") or 0.0), key=f"edit_treatment_area_{idx}")
                                product_row["area_ha"] = area_ha
                            quantity = round(float(product_row.get("dose") or 0.0) * max(float(product_row.get("area_ha") or 0.0), 0.0), 2)
                            st.caption(f"Ilość: {quantity:.2f}")
                            if product_row.get("product_name") and product_row.get("price_per_unit") >= 0:
                                estimated_cost = round(float(product_row.get("dose") or 0.0) * max(float(product_row.get("area_ha") or 0.0), 0.0) * float(product_row.get("price_per_unit") or 0.0), 2)
                                st.caption(f"Szacunkowy koszt: {estimated_cost:.2f} zł")
                            st.divider()

                    if st.button("Zapisz zmiany zabiegu", key="save_edited_treatment"):
                        valid_products = [
                            {
                                "category": row["category"],
                                "product_name": row["product_name"],
                                "price_per_unit": row["price_per_unit"],
                                "unit": row["unit"],
                                "dose": row["dose"],
                                "area_ha": row["area_ha"],
                            }
                            for row in st.session_state.edit_treatment_products
                            if row.get("product_name")
                        ]
                        primary_field_id = st.session_state.edit_treatment_selected_fields[0] if st.session_state.edit_treatment_selected_fields else st.session_state.get("edit_treatment_field_id")
                        if primary_field_id and edit_treatment_type and valid_products:
                            update_treatment(
                                treatment_id=selected_treatment_id,
                                field_id=int(primary_field_id),
                                treatment_date=edit_date.strftime("%Y-%m-%d"),
                                treatment_type=edit_treatment_type,
                                products=valid_products,
                                notes=notes_value,
                                crop_id=edit_crop_id,
                                crop_name=edit_crop_name,
                            )
                            st.session_state["edit_treatment_id"] = None
                            st.session_state["edit_treatment_selected_fields"] = []
                            st.session_state["edit_treatment_products"] = []
                            st.success("Zabieg zaktualizowany")
                            st.rerun()
                        else:
                            st.warning("Wybierz pole, sezon i dodaj co najmniej jeden produkt")
            else:
                st.info("Brak zabiegów do edycji")

        elif st.session_state.management_section == "treatments_add":
            st.subheader("Dodaj zabieg")
            if "treatment_selected_fields" not in st.session_state:
                st.session_state.treatment_selected_fields = []
            if "treatment_products" not in st.session_state:
                st.session_state.treatment_products = []
            if "treatment_last_selected_fields" not in st.session_state:
                st.session_state.treatment_last_selected_fields = []

            treatment_date = st.date_input("Data zabiegu", value=date.today())
            if not seasons_df.empty:
                season_options = {row["name"]: row["id"] for _, row in seasons_df.iterrows()}
                season_names = list(season_options.keys())
                default_season_name = ""
                if "is_default" in seasons_df.columns:
                    default_rows = seasons_df[seasons_df["is_default"].fillna(0).astype(int) == 1]
                    if not default_rows.empty:
                        default_season_name = str(default_rows.iloc[0]["name"])
                selected_season_name = st.selectbox(
                    "Sezon",
                    options=season_names,
                    index=season_names.index(default_season_name) if default_season_name in season_names else 0,
                    key="treatment_season",
                )
                season_id = season_options[selected_season_name]
                treatment_type = selected_season_name
            else:
                st.info("Najpierw dodaj sezon wegetacyjny")
                season_id = None
                treatment_type = ""

            @st.dialog("Wybierz pola do zabiegu", width="large")
            def open_treatment_field_picker_dialog() -> None:
                if fields_df.empty:
                    st.info("Najpierw dodaj pole")
                    return

                if "treatment_field_picker_pending_selected_fields" not in st.session_state:
                    st.session_state.treatment_field_picker_pending_selected_fields = list(
                        st.session_state.treatment_selected_fields
                    )

                if st_keyup is not None:
                    search_query = st_keyup(
                        "Wyszukaj pole po nazwie",
                        value=st.session_state.get("treatment_field_picker_search", ""),
                        key="treatment_field_picker_search",
                        placeholder="Wpisz nazwę pola...",
                        debounce=0,
                    )
                else:
                    search_query = st.text_input(
                        "Wyszukaj pole po nazwie",
                        value=st.session_state.get("treatment_field_picker_search", ""),
                        key="treatment_field_picker_search",
                        placeholder="Wpisz nazwę pola...",
                    )
                    st.caption("Live search po każdym znaku wymaga pakietu streamlit-keyup.")

                picker_rows = []
                pending_selected_ids = set(
                    int(field_id)
                    for field_id in st.session_state.get("treatment_field_picker_pending_selected_fields", [])
                )
                for _, field in fields_df.iterrows():
                    field_id = int(field["id"])
                    assignment = get_crop_assignment(field_id, int(season_id))
                    crop_name = ""
                    if assignment:
                        crop_name = str(assignment.get("crop_name") or "")

                    picker_rows.append(
                        {
                            "field_id": field_id,
                            "wybrano": field_id in pending_selected_ids,
                            "pole": str(field["name"]),
                            "uprawa": crop_name or "Brak przypisania",
                            "powierzchnia_ha": float(get_field_plot_area(field_id)),
                        }
                    )

                picker_df = pd.DataFrame(picker_rows)

                if search_query:
                    picker_df = picker_df[
                        picker_df["pole"].str.contains(str(search_query), case=False, na=False)
                    ]

                edited_picker_df = st.data_editor(
                    picker_df[["field_id", "wybrano", "pole", "uprawa", "powierzchnia_ha"]],
                    use_container_width=True,
                    hide_index=True,
                    disabled=["field_id", "pole", "uprawa", "powierzchnia_ha"],
                    column_config={
                        "field_id": None,
                        "wybrano": st.column_config.CheckboxColumn("Wybór"),
                        "pole": st.column_config.TextColumn("Pole"),
                        "uprawa": st.column_config.TextColumn("Uprawa"),
                        "powierzchnia_ha": st.column_config.NumberColumn("Powierzchnia [ha]", format="%.2f"),
                    },
                    key=f"treatment_field_picker_table_{int(season_id)}",
                )

                selected_ids = [
                    int(field_id)
                    for field_id in edited_picker_df.loc[edited_picker_df["wybrano"], "field_id"].tolist()
                ]

                visible_field_ids = {
                    int(field_id)
                    for field_id in edited_picker_df["field_id"].tolist()
                }
                merged_selected_ids = sorted(
                    (pending_selected_ids - visible_field_ids) | set(selected_ids)
                )
                st.session_state.treatment_field_picker_pending_selected_fields = merged_selected_ids

                st.caption(f"Zaznaczono pól: {len(merged_selected_ids)}")
                action_cols = st.columns([1, 1])
                with action_cols[0]:
                    if st.button("Zastosuj wybór", key=f"apply_treatment_field_picker_{int(season_id)}", use_container_width=True):
                        st.session_state.treatment_selected_fields = merged_selected_ids
                        st.rerun()
                with action_cols[1]:
                    if st.button("Anuluj", key=f"cancel_treatment_field_picker_{int(season_id)}", use_container_width=True):
                        st.session_state.treatment_field_picker_pending_selected_fields = list(
                            st.session_state.treatment_selected_fields
                        )
                        st.rerun()

            if fields_df.empty:
                st.info("Najpierw dodaj pole")
            else:
                can_open_field_picker = season_id is not None
                if st.button(
                    "Wybierz pole",
                    key="open_treatment_field_picker_dialog",
                    use_container_width=True,
                    disabled=not can_open_field_picker,
                ):
                    open_treatment_field_picker_dialog()
                if not can_open_field_picker:
                    st.caption("Najpierw wybierz sezon, aby aktywować wybór pól.")

            if st.session_state.treatment_selected_fields:
                selected_field_rows = []
                for field_id in st.session_state.treatment_selected_fields:
                    field_row = fields_df[fields_df["id"] == int(field_id)]
                    if not field_row.empty:
                        selected_field_rows.append(
                            {
                                "field_id": int(field_id),
                                "pole": str(field_row.iloc[0]["name"]),
                                "powierzchnia_ha": float(get_field_plot_area(int(field_id))),
                            }
                        )

                st.markdown("**Wybrane pola**")
                selected_fields_df = pd.DataFrame(selected_field_rows)

                header_cols = st.columns([3.2, 1.6, 0.8])
                header_cols[0].markdown("**Pole**")
                header_cols[1].markdown("**Powierzchnia [ha]**")
                header_cols[2].markdown("")

                for _, row in selected_fields_df.iterrows():
                    cols = st.columns([3.2, 1.6, 0.8])
                    cols[0].write(str(row["pole"]))
                    cols[1].write(f"{float(row['powierzchnia_ha']):.2f}")
                    if cols[2].button("✕", key=f"remove_treatment_field_{int(row['field_id'])}", use_container_width=True):
                        st.session_state.treatment_selected_fields.remove(int(row["field_id"]))
                        st.session_state.treatment_last_selected_fields = list(st.session_state.treatment_selected_fields)
                        st.rerun()

                total_selected_area = float(selected_fields_df["powierzchnia_ha"].sum())
                st.metric("Suma powierzchni", f"{total_selected_area:.2f} ha")
            else:
                st.info("Brak wybranych pól. Dodaj przynajmniej jedno pole.")

            if st.session_state.treatment_selected_fields:
                total_selected_area = float(
                    sum(get_field_plot_area(int(field_id)) for field_id in st.session_state.treatment_selected_fields)
                )
            else:
                total_selected_area = 0.0

            if st.session_state.treatment_last_selected_fields != st.session_state.treatment_selected_fields:
                for product_row in st.session_state.treatment_products:
                    product_row["area_ha"] = total_selected_area
                st.session_state.treatment_last_selected_fields = list(st.session_state.treatment_selected_fields)

            crop_display_text = ""
            selected_crop_id = None
            selected_crop_name = ""
            if st.session_state.treatment_selected_fields and season_id is not None:
                crop_names = []
                crop_ids = []
                for field_id in st.session_state.treatment_selected_fields:
                    assignment = get_crop_assignment(int(field_id), season_id)
                    if assignment:
                        crop_name = str(assignment.get("crop_name") or "")
                        crop_id = int(assignment["crop_id"]) if assignment.get("crop_id") is not None else None
                        crop_names.append(crop_name)
                        crop_ids.append(crop_id)
                    else:
                        crop_names.append("")
                        crop_ids.append(None)

                unique_crop_names = [name for name in crop_names if name]
                if unique_crop_names and len(set(unique_crop_names)) > 1:
                    crop_display_text = "Wiele upraw"
                elif unique_crop_names:
                    crop_display_text = unique_crop_names[0]
                    selected_crop_id = crop_ids[0]
                    selected_crop_name = unique_crop_names[0]
                else:
                    crop_display_text = "Nie wybrana uprawa. Uzupełnij w Raporty -> Płodozmian"

                st.caption(f"Przypisana uprawa: {crop_display_text}")
            elif st.session_state.treatment_selected_fields:
                st.caption("Wybierz sezon, aby zobaczyć przypisaną uprawę.")
            else:
                st.caption("Dodaj przynajmniej jedno pole, aby zobaczyć przypisaną uprawę.")

            @st.dialog("Wybierz produkt do zabiegu", width="large")
            def open_treatment_product_picker_dialog() -> None:
                product_rows = []
                owner_username = _current_owner()
                for category_name in PRODUCT_TABLES:
                    category_catalog = load_product_catalog(category_name, owner_username)
                    if category_catalog.empty:
                        continue
                    for _, product in category_catalog.iterrows():
                        product_name = str(product.get("name") or "").strip()
                        if not product_name:
                            continue
                        unit_value = str(product.get("unit") or "")
                        notes_value = str(product.get("notes") or "")
                        product_rows.append(
                            {
                                "product_key": f"{category_name}::{product_name}",
                                "nazwa": product_name,
                                "kategoria": category_name,
                                "jednostka": unit_value,
                                "notatki": notes_value,
                                "price_per_unit": float(product.get("price_per_unit") or 0.0),
                            }
                        )

                if not product_rows:
                    st.info("Brak produktów w katalogach")
                    return

                if "treatment_product_picker_category_filter" not in st.session_state:
                    st.session_state.treatment_product_picker_category_filter = "ALL"
                if "treatment_product_picker_pending_selected_keys" not in st.session_state:
                    st.session_state.treatment_product_picker_pending_selected_keys = []

                category_filter_cols = st.columns(len(PRODUCT_TABLES))
                current_category_filter = str(st.session_state.get("treatment_product_picker_category_filter") or "ALL")
                for idx, category_name in enumerate(PRODUCT_TABLES):
                    is_active_filter = current_category_filter == category_name
                    filter_button_label = f"● {category_name}" if is_active_filter else category_name
                    if category_filter_cols[idx].button(
                        filter_button_label,
                        key=f"treatment_product_filter_{category_name}",
                        use_container_width=True,
                    ):
                        st.session_state.treatment_product_picker_category_filter = (
                            "ALL" if is_active_filter else category_name
                        )

                active_filter = str(st.session_state.get("treatment_product_picker_category_filter") or "ALL")
                if active_filter == "ALL":
                    st.caption("Filtr kategorii: wszystkie")
                else:
                    st.caption(f"Filtr kategorii: {active_filter}")

                if st_keyup is not None:
                    search_query = st_keyup(
                        "Wyszukaj produkt (nazwa + notatki)",
                        value=st.session_state.get("treatment_product_picker_search", ""),
                        key="treatment_product_picker_search",
                        placeholder="Wpisz fragment nazwy lub notatki...",
                        debounce=0,
                    )
                else:
                    search_query = st.text_input(
                        "Wyszukaj produkt (nazwa + notatki)",
                        value=st.session_state.get("treatment_product_picker_search", ""),
                        key="treatment_product_picker_search",
                        placeholder="Wpisz fragment nazwy lub notatki...",
                    )
                    st.caption("Live search po każdym znaku wymaga pakietu streamlit-keyup.")

                all_products_df = pd.DataFrame(product_rows)
                picker_products_df = all_products_df.copy()
                if active_filter != "ALL":
                    picker_products_df = picker_products_df[
                        picker_products_df["kategoria"] == active_filter
                    ]

                if search_query:
                    search_value = str(search_query)
                    picker_products_df = picker_products_df[
                        picker_products_df["nazwa"].str.contains(search_value, case=False, na=False)
                        | picker_products_df["notatki"].str.contains(search_value, case=False, na=False)
                    ]

                pending_selected_product_keys = set(
                    str(product_key)
                    for product_key in st.session_state.get("treatment_product_picker_pending_selected_keys", [])
                )
                picker_products_df = picker_products_df.sort_values(by=["nazwa", "kategoria"], kind="stable").reset_index(drop=True)
                picker_products_df["wybrano"] = picker_products_df["product_key"].isin(pending_selected_product_keys)

                edited_products_df = st.data_editor(
                    picker_products_df[["product_key", "wybrano", "nazwa", "kategoria", "jednostka", "notatki"]],
                    use_container_width=True,
                    hide_index=True,
                    disabled=["product_key", "nazwa", "kategoria", "jednostka", "notatki"],
                    column_config={
                        "product_key": None,
                        "wybrano": st.column_config.CheckboxColumn("Wybór"),
                        "nazwa": st.column_config.TextColumn("Nazwa"),
                        "kategoria": st.column_config.TextColumn("Kategoria"),
                        "jednostka": st.column_config.TextColumn("Jednostka"),
                        "notatki": st.column_config.TextColumn("Notatki"),
                    },
                    key="treatment_product_picker_table",
                )

                checked_product_keys = edited_products_df.loc[
                    edited_products_df["wybrano"], "product_key"
                ].tolist()
                visible_product_keys = {
                    str(product_key)
                    for product_key in edited_products_df["product_key"].tolist()
                }
                merged_selected_product_keys = sorted(
                    (pending_selected_product_keys - visible_product_keys)
                    | {str(product_key) for product_key in checked_product_keys}
                )
                st.session_state.treatment_product_picker_pending_selected_keys = merged_selected_product_keys

                st.caption(f"Zaznaczono produktów: {len(merged_selected_product_keys)}")

                product_picker_action_cols = st.columns([1, 1])
                with product_picker_action_cols[0]:
                    if st.button("Dodaj zaznaczone produkty", key="add_selected_treatment_product", use_container_width=True):
                        if not merged_selected_product_keys:
                            st.warning("Zaznacz co najmniej jeden produkt")
                        else:
                            selected_products_df = all_products_df[
                                all_products_df["product_key"].isin(merged_selected_product_keys)
                            ].sort_values(by=["nazwa", "kategoria"], kind="stable")

                            for _, selected_product_row in selected_products_df.iterrows():
                                st.session_state.treatment_products.append(
                                    {
                                        "category": str(selected_product_row["kategoria"]),
                                        "product_name": str(selected_product_row["nazwa"]),
                                        "price_per_unit": float(selected_product_row["price_per_unit"]),
                                        "unit": str(selected_product_row["jednostka"]),
                                        "dose": 1.0 if str(selected_product_row["kategoria"]) == "Maszyny" else 0.0,
                                        "area_ha": total_selected_area,
                                    }
                                )
                            st.session_state.treatment_product_picker_pending_selected_keys = []
                            st.rerun()
                with product_picker_action_cols[1]:
                    if st.button("Anuluj", key="cancel_treatment_product_picker", use_container_width=True):
                        st.session_state.treatment_product_picker_pending_selected_keys = []
                        st.rerun()

            product_action_cols = st.columns([1, 1])
            with product_action_cols[0]:
                if st.button("Dodaj produkt", key="add_treatment_product", use_container_width=True):
                    st.session_state.treatment_product_picker_pending_selected_keys = []
                    open_treatment_product_picker_dialog()
            with product_action_cols[1]:
                if st.button("Usuń", key="remove_last_treatment_product", use_container_width=True, disabled=not st.session_state.treatment_products):
                    if st.session_state.treatment_products:
                        st.session_state.treatment_products.pop()
                        st.rerun()

            notes = st.text_area("Opis")

            if st.session_state.treatment_products:
                for idx, product_row in enumerate(st.session_state.treatment_products):
                    st.markdown(f"### Produkt {idx + 1}")
                    col1, col2, col3, col4 = st.columns([2, 2, 1.5, 1.5])
                    with col1:
                        current_category = product_row.get("category") or PRODUCT_TABLES[0]
                        category_index = PRODUCT_TABLES.index(current_category) if current_category in PRODUCT_TABLES else 0
                        category = st.selectbox(
                            "Kategoria",
                            options=PRODUCT_TABLES,
                            index=category_index,
                            key=f"treatment_category_{idx}",
                        )
                        if category != product_row.get("category"):
                            product_row["category"] = category
                            product_row["product_name"] = ""
                            product_row["price_per_unit"] = 0.0
                            product_row["unit"] = ""
                            product_row["dose"] = 1.0 if category == "Maszyny" else 0.0
                        else:
                            product_row["category"] = category

                    with col2:
                        product_catalog = load_product_catalog(product_row["category"], _current_owner())
                        if not product_catalog.empty:
                            product_names = list(product_catalog["name"])
                            current_product = product_row.get("product_name") or ""
                            product_index = product_names.index(current_product) if current_product in product_names else 0
                            selected_product_name = st.selectbox(
                                "Produkt",
                                options=product_names,
                                index=product_index,
                                key=f"treatment_product_{idx}",
                            )
                            selected_row = product_catalog[product_catalog["name"] == selected_product_name].iloc[0]
                            product_row["product_name"] = selected_product_name
                            product_row["price_per_unit"] = float(selected_row["price_per_unit"])
                            product_row["unit"] = selected_row["unit"]
                        else:
                            st.info("Brak produktów w tej kategorii")
                            product_row["product_name"] = ""
                            product_row["price_per_unit"] = 0.0
                            product_row["unit"] = ""

                    with col3:
                        dose = st.number_input(
                            "Dawka na ha",
                            min_value=0.0,
                            step=0.1,
                            value=float(product_row.get("dose") or 0.0),
                            key=f"treatment_dose_{idx}",
                        )
                        product_row["dose"] = dose

                    with col4:
                        area_ha = st.number_input(
                            "Powierzchnia zabiegu [ha]",
                            min_value=0.0,
                            step=0.1,
                            value=float(product_row.get("area_ha") or 0.0),
                            key=f"treatment_area_{idx}",
                        )
                        product_row["area_ha"] = area_ha

                    quantity = round(float(product_row.get("dose") or 0.0) * max(float(product_row.get("area_ha") or 0.0), 0.0), 2)
                    quantity_unit = str(product_row.get("unit") or "").strip()
                    quantity_suffix = f" {quantity_unit}" if quantity_unit else ""
                    st.caption(f"Ilość: {quantity:.2f}{quantity_suffix}")
                    if product_row.get("product_name") and product_row.get("price_per_unit") >= 0:
                        estimated_cost = round(float(product_row.get("dose") or 0.0) * max(float(product_row.get("area_ha") or 0.0), 0.0) * float(product_row.get("price_per_unit") or 0.0), 2)
                        st.caption(f"Szacunkowy koszt: {estimated_cost:.2f} zł")
                    st.divider()

            if st.button("Zapisz zabieg", key="submit_treatment"):
                valid_products = [
                    {
                        "category": row["category"],
                        "product_name": row["product_name"],
                        "price_per_unit": row["price_per_unit"],
                        "unit": row["unit"],
                        "dose": row["dose"],
                        "area_ha": row["area_ha"],
                    }
                    for row in st.session_state.treatment_products
                    if row.get("product_name")
                ]
                if st.session_state.treatment_selected_fields and treatment_type and valid_products:
                    try:
                        inserted_count = save_treatments(
                            field_ids=st.session_state.treatment_selected_fields,
                            treatment_date=treatment_date.strftime("%Y-%m-%d"),
                            treatment_type=treatment_type,
                            products=valid_products,
                            notes=notes,
                            crop_id=selected_crop_id,
                            crop_name=selected_crop_name,
                        )
                    except Exception as exc:
                        st.error(f"Nie udało się zapisać zabiegu: {exc}")
                    else:
                        if inserted_count > 0:
                            st.success("Zabieg zapisano")
                            time.sleep(2.5)
                            st.session_state.treatment_products = []
                            st.session_state.treatment_selected_fields = []
                            st.rerun()
                        else:
                            st.warning("Nie udało się zaksięgować zabiegu. Formularz nie został wyczyszczony.")
                else:
                    st.warning("Wybierz przynajmniej jedno pole, sezon i dodaj co najmniej jeden produkt")

        elif st.session_state.management_section == "products":
            st.subheader("Zarządzanie katalogami produktów")
            selected_category = st.selectbox("Wybierz kategorię", options=PRODUCT_TABLES, key="catalog_category")

            st.markdown(f"### {selected_category}")

            if selected_category == "ŚOR":
                sor_registry_df, sor_registry_path, sor_registry_error = load_sor_registry()
                with st.expander("Importuj ŚOR z rejestru Excel", expanded=True):
                    if sor_registry_df.empty:
                        if sor_registry_error:
                            st.warning(f"Plik {sor_registry_path} istnieje, ale nie można go odczytać: {sor_registry_error}")
                        else:
                            if os.path.exists(sor_registry_path):
                                st.warning(f"Plik {sor_registry_path} istnieje, ale jest pusty lub ma nieobsługiwany format.")
                            else:
                                st.warning(f"Plik {sor_registry_path} nie został znaleziony.")
                    else:
                        sor_search_query = st.text_input(
                            "Wyszukaj po nazwie środka ochrony roślin",
                            value=st.session_state.get("sor_search_query", ""),
                            key="sor_search_query",
                        )
                        st.button("Szukaj w rejestrze ŚOR", key="search_sor_button")
                        if sor_search_query:
                            sor_search_results = search_sor_items(sor_registry_df, sor_search_query)
                            if sor_search_results.empty:
                                st.info("Brak wyników dla podanej nazwy.")
                            else:
                                sor_name_col = find_sor_column(sor_search_results, ["Nazwa środka ochrony roślin", "Nazwa środka"])
                                st.markdown("**Znalezione rekordy ŚOR**")
                                st.dataframe(
                                    sor_search_results.head(20),
                                    use_container_width=True,
                                    hide_index=True,
                                )
                            display_map = {}
                            for idx in sor_search_results.index.tolist():
                                row = sor_search_results.loc[idx]
                                display_label = str(row.get(sor_name_col) or "").strip()
                                display_map[idx] = f"{display_label}"
                            selected_sor_index = st.selectbox(
                                "Wybierz rekord ŚOR do importu",
                                options=sor_search_results.index.tolist(),
                                format_func=lambda x: display_map.get(x, str(x)),
                                key="sor_select_index",
                            )
                            if st.button("Importuj wybrany produkt ŚOR", key="import_sor_button"):
                                selected_row = sor_search_results.loc[selected_sor_index]
                                sor_name_col = find_sor_column(selected_row.to_frame().T, ["Nazwa środka ochrony roślin", "Nazwa środka"])
                                product_name = str(selected_row.get(sor_name_col) or "").strip() if sor_name_col else ""
                                if not product_name:
                                    st.error("Wybrany rekord nie zawiera nazwy produktu ŚOR.")
                                elif sor_product_exists(product_name):
                                    st.warning("Produkt o tej nazwie już istnieje w katalogu ŚOR.")
                                elif import_sor_product(selected_row):
                                    st.success("Produkt ŚOR został zaimportowany do katalogu.")
                                    st.rerun()
                                else:
                                    st.error("Nie udało się zaimportować produktu ŚOR.")

            with st.form(f"form_{selected_category}", clear_on_submit=True):
                product_name = st.text_input("Nazwa produktu", key=f"{selected_category}_name")
                price_per_unit = st.number_input("Cena jednostkowa [zł]", min_value=0.0, step=0.1, key=f"{selected_category}_price")
                if selected_category == "Nawozy":
                    unit = st.selectbox("Jednostka", options=["kg", "t"], key=f"{selected_category}_unit")
                else:
                    unit = st.text_input("Jednostka", key=f"{selected_category}_unit")
                notes = st.text_area("Notatki", key=f"{selected_category}_notes")
                if selected_category == "Nawozy":
                    st.markdown("**Zawartość składników pokarmowych [%]**")
                    nawoz_cols = st.columns(5)
                    n_pct = nawoz_cols[0].number_input("N", min_value=0.0, max_value=100.0, step=0.1, key="nawoz_n")
                    p2o5_pct = nawoz_cols[1].number_input("P₂O₅", min_value=0.0, max_value=100.0, step=0.1, key="nawoz_p2o5")
                    k2o_pct = nawoz_cols[2].number_input("K₂O", min_value=0.0, max_value=100.0, step=0.1, key="nawoz_k2o")
                    so3_pct = nawoz_cols[3].number_input("SO₃", min_value=0.0, max_value=100.0, step=0.1, key="nawoz_so3")
                    cao_pct = nawoz_cols[4].number_input("CaO", min_value=0.0, max_value=100.0, step=0.1, key="nawoz_cao")
                submitted = st.form_submit_button("Zapisz produkt", key=f"{selected_category}_submit")
                if submitted:
                    if product_name:
                        if selected_category == "Nawozy":
                            save_nawoz_product(product_name, price_per_unit, unit, notes, n_pct, p2o5_pct, k2o_pct, so3_pct, cao_pct)
                        else:
                            save_product(selected_category, product_name, price_per_unit, unit, notes)
                        st.success(f"Produkt dodany do {selected_category}")
                        st.rerun()
                    else:
                        st.warning("Podaj nazwę produktu")

            catalog_df = load_product_catalog(selected_category, _current_owner())
            if catalog_df.empty:
                st.info("Brak produktów w tej kategorii")
            else:
                nawoz_extra_config = {
                    "n_pct": st.column_config.NumberColumn("N [%]", min_value=0.0, max_value=100.0),
                    "p2o5_pct": st.column_config.NumberColumn("P₂O₅ [%]", min_value=0.0, max_value=100.0),
                    "k2o_pct": st.column_config.NumberColumn("K₂O [%]", min_value=0.0, max_value=100.0),
                    "so3_pct": st.column_config.NumberColumn("SO₃ [%]", min_value=0.0, max_value=100.0),
                    "cao_pct": st.column_config.NumberColumn("CaO [%]", min_value=0.0, max_value=100.0),
                    "unit": st.column_config.SelectboxColumn("Jednostka", options=["kg", "t"]),
                } if selected_category == "Nawozy" else {}
                edited_catalog = st.data_editor(
                    catalog_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True),
                        "name": st.column_config.TextColumn("Nazwa"),
                        "price_per_unit": st.column_config.NumberColumn("Cena [zł]"),
                        "unit": st.column_config.TextColumn("Jednostka"),
                        "notes": st.column_config.TextColumn("Notatki"),
                        **nawoz_extra_config,
                    },
                    disabled=["id"],
                    key=f"editor_{selected_category}",
                )

                if st.button("Zapisz zmiany w tej kategorii", key=f"save_{selected_category}"):
                    for _, row in edited_catalog.iterrows():
                        if selected_category == "Nawozy":
                            update_nawoz_product(
                                int(row["id"]),
                                str(row["name"]),
                                float(row["price_per_unit"]),
                                str(row["unit"]),
                                str(row["notes"]),
                                float(row.get("n_pct") or 0.0),
                                float(row.get("p2o5_pct") or 0.0),
                                float(row.get("k2o_pct") or 0.0),
                                float(row.get("so3_pct") or 0.0),
                                float(row.get("cao_pct") or 0.0),
                            )
                        else:
                            update_product(selected_category, int(row["id"]), str(row["name"]), float(row["price_per_unit"]), str(row["unit"]), str(row["notes"]))
                    st.success("Zapisano zmiany")
                    st.rerun()

                delete_options = {f"{row['name']} ({row['unit']})": int(row["id"]) for _, row in catalog_df.iterrows()}
                selected_delete_name = st.selectbox(
                    "Wybierz produkt do usunięcia",
                    options=list(delete_options.keys()),
                    key=f"delete_select_{selected_category}",
                )
                if st.button("Usuń wybrany produkt", key=f"delete_{selected_category}"):
                    delete_product(selected_category, delete_options[selected_delete_name])
                    st.success("Produkt usunięty")
                    st.rerun()

        elif st.session_state.management_section == "costs":
            st.subheader("Lista kosztów")
            st.dataframe(costs_df, use_container_width=True, hide_index=True)

        elif st.session_state.management_section == "users" and is_admin:
            show_admin_user_management_panel()

    with tab3:
        st.subheader("Raporty")
        st.caption("Wybierz raport, aby przejść do odpowiednich formularzy i wyników.")

        if "report_section" not in st.session_state:
            st.session_state.report_section = "crop_rotation"

        report_buttons = st.columns(5)
        with report_buttons[0]:
            if st.button("Płodozmian", key="report_section_crop_rotation", use_container_width=True):
                st.session_state.report_section = "crop_rotation"
        with report_buttons[1]:
            if st.button("Kartoteka uprawy", key="report_section_crop_report", use_container_width=True):
                st.session_state.report_section = "crop_report"
        with report_buttons[2]:
            if st.button("Kartoteka pola", key="report_section_field_report", use_container_width=True):
                st.session_state.report_section = "field_report"
        with report_buttons[3]:
            if st.button("Ewidencja zabiegów", key="report_section_registry", use_container_width=True):
                st.session_state.report_section = "registry"
        with report_buttons[4]:
            if st.button("Zużycie", key="report_section_consumption", use_container_width=True):
                st.session_state.report_section = "consumption"

        st.divider()

        if st.session_state.report_section == "crop_rotation":
            st.subheader("Płodozmian")
            st.caption("Zaznacz sezony wegetacyjne, a następnie zaktualizuj przypisania upraw do pól w tabeli.")

            if seasons_df.empty:
                st.info("Brak sezonów. Dodaj sezon wegetacyjny, aby utworzyć płodozmian.")
            else:
                selected_rotation_seasons = []
                for _, season in seasons_df.iterrows():
                    season_selected = st.checkbox(str(season["name"]), key=f"rotation_season_{int(season['id'])}")
                    if season_selected:
                        selected_rotation_seasons.append(str(season["name"]))

                if selected_rotation_seasons:
                    rotation_df = build_crop_rotation_table(fields_df, seasons_df, crop_assignments_df, selected_rotation_seasons)
                    crop_options = ["", *list(crops_df["name"])]
                    column_config = {
                        "field_id": st.column_config.NumberColumn("ID", disabled=True),
                        "pole": st.column_config.TextColumn("Pole", disabled=True),
                    }
                    for season_name in selected_rotation_seasons:
                        column_config[season_name] = st.column_config.SelectboxColumn(
                            season_name,
                            options=crop_options,
                            required=False,
                        )

                    edited_rotation_df = st.data_editor(
                        rotation_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config=column_config,
                        disabled=["field_id", "pole"],
                        key="crop_rotation_editor",
                    )

                    render_crop_rotation_progress_charts(edited_rotation_df, selected_rotation_seasons, fields_df)

                    if st.button("Zapisz płodozmian", key="save_crop_rotation"):
                        season_id_lookup = {str(row["name"]): int(row["id"]) for _, row in seasons_df.iterrows()}
                        crop_id_lookup = {str(row["name"]): int(row["id"]) for _, row in crops_df.iterrows()}
                        for _, row in edited_rotation_df.iterrows():
                            field_id = int(row["field_id"])
                            for season_name in selected_rotation_seasons:
                                crop_name = str(row.get(season_name, "") or "").strip()
                                crop_id = crop_id_lookup.get(crop_name)
                                save_crop_assignment(field_id, season_id_lookup[season_name], crop_id)
                        st.success("Płodozmian zapisany")
                        st.rerun()
                else:
                    st.info("Zaznacz przynajmniej jeden sezon, aby utworzyć raport płodozmian.")

        elif st.session_state.report_section == "crop_report":
            st.subheader("Kartoteka uprawy")
            if crops_df.empty:
                st.info("Brak upraw. Dodaj uprawę, aby wygenerować raport.")
            else:
                selected_crop_name = st.selectbox("Wybierz uprawę", options=list(crops_df["name"]), key="crop_report_crop")

                if seasons_df.empty:
                    st.info("Brak sezonów. Dodaj sezon wegetacyjny.")
                    selected_crop_season = ""
                else:
                    selected_crop_season = st.selectbox(
                        "Wybierz sezon wegetacyjny",
                        options=list(seasons_df["name"]),
                        key="crop_report_season",
                    )

                if st.button("Generuj kartotekę uprawy", key="generate_crop_report"):
                    crop_report_df, total_area_ha, total_cost, cost_per_ha = build_crop_report(
                        selected_crop_name,
                        selected_crop_season,
                        treatments_df,
                        costs_df,
                    )

                    st.markdown("### Wyniki kartoteki uprawy")
                    if crop_report_df.empty:
                        st.info("Brak zabiegów dla wybranej uprawy i sezonu.")
                    else:
                        left_col, right_col = st.columns([1.5, 1])
                        with left_col:
                            st.metric("Suma powierzchni uprawy", f"{total_area_ha:,.2f} ha")
                            st.metric("Suma kosztów", f"{total_cost:,.2f} zł")
                            st.metric("Suma kosztów/ha", f"{cost_per_ha:,.2f} zł/ha")
                        with right_col:
                            st.markdown("<div style='margin-top: 0.2rem;'></div>", unsafe_allow_html=True)
                            if "product_cost_pln" in crop_report_df.columns:
                                category_costs = crop_report_df[crop_report_df["product_cost_pln"] > 0].groupby("product_category", dropna=False)["product_cost_pln"].sum()
                                if not category_costs.empty:
                                    render_category_donut_chart(category_costs)
                        st.dataframe(crop_report_df, use_container_width=True, hide_index=True)
                        nutrient_summary_df = build_fertilizer_nutrient_summary(crop_report_df, total_area_ha)
                        st.markdown("#### Suma dostarczonych składników z nawozów [kg/ha]")
                        if nutrient_summary_df.empty:
                            st.info("Brak produktów z kategorii Nawozy do wyliczenia składników.")
                        else:
                            st.dataframe(nutrient_summary_df, use_container_width=True, hide_index=True)

        elif st.session_state.report_section == "field_report":
            st.subheader("Kartoteka pola")
            if fields_df.empty:
                st.info("Brak pól. Dodaj pole, aby wygenerować raport.")
            else:
                field_options = {row["name"]: int(row["id"]) for _, row in fields_df.iterrows()}
                selected_report_field = st.selectbox("Wybierz pole", options=list(field_options.keys()), key="report_field")
                selected_report_field_id = field_options[selected_report_field]

                if seasons_df.empty:
                    st.info("Brak sezonów. Dodaj sezon wegetacyjny.")
                    selected_report_season = ""
                else:
                    selected_report_season = st.selectbox(
                        "Wybierz sezon wegetacyjny",
                        options=list(seasons_df["name"]),
                        key="report_season",
                    )

                if st.button("Generuj raport", key="generate_field_report"):
                    report_df, total_cost, cost_per_ha = build_field_report(
                        selected_report_field_id,
                        selected_report_season,
                        treatments_df,
                        costs_df,
                    )

                    st.markdown("### Wyniki raportu")
                    if report_df.empty:
                        st.info("Brak zabiegów dla wybranego pola i sezonu.")
                    else:
                        left_col, right_col = st.columns([1.5, 1])
                        with left_col:
                            st.metric("Suma kosztów", f"{total_cost:,.2f} zł")
                            st.metric("Koszty na ha", f"{cost_per_ha:,.2f} zł/ha")
                        with right_col:
                            st.markdown("<div style='margin-top: 0.2rem;'></div>", unsafe_allow_html=True)
                            if "product_cost_pln" in report_df.columns:
                                category_costs = report_df[report_df["product_cost_pln"] > 0].groupby("product_category", dropna=False)["product_cost_pln"].sum()
                                if not category_costs.empty:
                                    render_category_donut_chart(category_costs)
                        st.dataframe(report_df, use_container_width=True, hide_index=True)
                        reference_area_ha = get_field_plot_area(selected_report_field_id)
                        nutrient_summary_df = build_fertilizer_nutrient_summary(report_df, reference_area_ha)
                        st.markdown("#### Suma dostarczonych składników z nawozów [kg/ha]")
                        if nutrient_summary_df.empty:
                            st.info("Brak produktów z kategorii Nawozy do wyliczenia składników.")
                        else:
                            st.dataframe(nutrient_summary_df, use_container_width=True, hide_index=True)

        elif st.session_state.report_section == "registry":
            st.subheader("Ewidencja zabiegów")
            if farms_df.empty:
                st.info("Brak gospodarstw. Dodaj gospodarstwo, aby wygenerować raport.")
            else:
                farm_options = {row["name"]: int(row["id"]) for _, row in farms_df.iterrows()}
                selected_report_farm = st.selectbox("Wybierz gospodarstwo", options=list(farm_options.keys()), key="registry_farm")
                selected_report_farm_id = farm_options[selected_report_farm]

                if seasons_df.empty:
                    st.info("Brak sezonów. Dodaj sezon wegetacyjny.")
                    selected_registry_season = ""
                else:
                    selected_registry_season = st.selectbox(
                        "Wybierz sezon wegetacyjny",
                        options=list(seasons_df["name"]),
                        key="registry_season",
                    )

                registry_group_fields = st.checkbox("Grupuj działki", key="registry_group_fields", value=False)

                if st.button("Generuj ewidencję zabiegów", key="generate_registry_report"):
                    registry_report_df = build_treatment_registry_report(
                        selected_report_farm_id,
                        selected_registry_season,
                        farms_df,
                        plots_df,
                        fields_df,
                        treatments_df,
                        group_fields=registry_group_fields,
                    )
                    st.markdown("### Wyniki ewidencji zabiegów")
                    if registry_report_df.empty:
                        st.info("Brak działek lub zabiegów dla wybranego gospodarstwa i sezonu.")
                    else:
                        registry_display_df = registry_report_df.rename(
                            columns={
                                "plot_name": "nr działki",
                                "treatment_date": "data",
                                "season": "sezon",
                                "uprawa": "uprawa",
                                "product_category": "kategoria",
                                "product_name": "zastosowany produkt",
                                "dose": "dawka",
                                "area_ha": "powierzchnia",
                            }
                        )
                        ordered_columns = ["nr działki", "kategoria", "zastosowany produkt", "data", "dawka", "powierzchnia", "uprawa", "sezon"]
                        registry_display_df = registry_display_df[[col for col in ordered_columns if col in registry_display_df.columns]].copy()
                        st.dataframe(registry_display_df, use_container_width=True, hide_index=True)

                        registry_export_df = registry_report_df.copy()
                        registry_export_df = registry_export_df.rename(columns={
                            "plot_name": "nr działki",
                            "product_category": "kategoria",
                            "product_name": "zastosowany produkt",
                            "treatment_date": "data",
                            "dose": "dawka",
                            "area_ha": "powierzchnia",
                            "uprawa": "uprawa",
                            "season": "sezon",
                        })
                        registry_export_df = registry_export_df[[col for col in ordered_columns if col in registry_export_df.columns]].copy()
                        st.download_button(
                            "Eksportuj ewidencję zabiegów do XLSX",
                            data=to_excel_bytes(registry_export_df),
                            file_name="ewidencja_zabiegow.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="download_registry_report",
                        )

                if st.button("Generuj ewidencję ŚOR", key="generate_sor_registry_report"):
                    sor_registry_report_df = build_treatment_registry_report(
                        selected_report_farm_id,
                        selected_registry_season,
                        farms_df,
                        plots_df,
                        fields_df,
                        treatments_df,
                        category_filter="ŚOR",
                        group_fields=registry_group_fields,
                    )
                    st.session_state["sor_registry_report_df"] = sor_registry_report_df
                    st.session_state["sor_registry_ai_rows"] = []
                    st.session_state["sor_registry_analysis_done"] = False

                sor_registry_report_df = st.session_state.get("sor_registry_report_df")
                st.markdown("### Wyniki ewidencji ŚOR")
                if sor_registry_report_df is None or sor_registry_report_df.empty:
                    st.info("Brak zabiegów ŚOR dla wybranego gospodarstwa i sezonu.")
                else:
                    sor_registry_display_df = sor_registry_report_df.rename(
                        columns={
                            "plot_name": "nr działki",
                            "treatment_date": "data",
                            "season": "sezon",
                            "uprawa": "uprawa",
                            "product_category": "kategoria",
                            "product_name": "zastosowany produkt",
                            "dose": "dawka",
                            "area_ha": "powierzchnia",
                        }
                    )
                    ordered_columns = ["nr działki", "kategoria", "zastosowany produkt", "data", "dawka", "powierzchnia", "uprawa", "sezon"]
                    sor_registry_display_df = sor_registry_display_df[[col for col in ordered_columns if col in sor_registry_display_df.columns]].copy()

                    @st.dialog("Uzasadnienie niezgodności AI")
                    def show_ai_noncompliance_dialog(reasons: list[dict]) -> None:
                        for item in reasons:
                            st.markdown(f"### {item.get('zastosowany produkt', 'Produkt')}")
                            st.markdown(item.get("uzasadnienie", "Brak uzasadnienia."))
                            st.caption(f"Status: {item.get('status', 'unknown')}")
                            st.divider()

                    if st.button("Uruchom analizę AI", key="run_sor_ai_analysis"):
                        ai_rows = []
                        non_compliance_rows = []
                        for _, row in sor_registry_report_df.iterrows():
                            product_name = str(row.get("product_name") or "").strip()
                            crop_name = str(row.get("uprawa") or "").strip()
                            dose_value = row.get("dose")
                            date_value = row.get("treatment_date")
                            notes_value = ""
                            if "notes" in treatments_df.columns:
                                matching_rows = treatments_df[
                                    (treatments_df["field_id"].astype(str) == str(row.get("field_id")))
                                    & (treatments_df["treatment_date"].astype(str) == str(date_value))
                                ]
                                if not matching_rows.empty:
                                    notes_value = str(matching_rows.iloc[0].get("notes") or "")
                            analysis = analyze_sor_row_with_groq(
                                product_name=product_name,
                                crop_name=crop_name,
                                dose=dose_value,
                                application_date=date_value,
                                sor_notes=notes_value,
                            )
                            ai_row = {
                                "status": str(analysis.get("overall_status", "unknown")).strip(),
                                "uzasadnienie": str(analysis.get("summary", "Brak uzasadnienia.")),
                                "zastosowany produkt": product_name,
                                "debug_logs": analysis.get("debug_logs", []),
                            }
                            ai_rows.append(ai_row)
                            if str(analysis.get("overall_status", "unknown")).lower() == "non_compliant":
                                non_compliance_rows.append(ai_row)

                        st.session_state["sor_registry_ai_rows"] = ai_rows
                        st.session_state["sor_registry_analysis_done"] = True
                        st.success("Analiza AI zakończona.")

                        if non_compliance_rows:
                            show_ai_noncompliance_dialog(non_compliance_rows)

                    ai_rows = st.session_state.get("sor_registry_ai_rows", [])
                    if ai_rows:
                        ai_status_map = {item["zastosowany produkt"]: item["status"] for item in ai_rows}
                        ai_display_df = sor_registry_display_df.copy()

                        def _style_sor_ai_rows(row):
                            product = str(row.get("zastosowany produkt", "")).strip()
                            status = str(ai_status_map.get(product, "unknown")).lower()
                            if status == "non_compliant":
                                return ["background-color: #f8d7da; color: #111111" for _ in row]
                            if status == "unknown":
                                return ["background-color: #e2e3e5; color: #111111" for _ in row]
                            return ["background-color: #d4edda; color: #111111" for _ in row]

                        styled = ai_display_df.style.apply(_style_sor_ai_rows, axis=1)
                        st.dataframe(styled, use_container_width=True, hide_index=True)
                        
                        st.markdown("#### Logi analizy AI")
                        for ai_row in ai_rows:
                            with st.expander(f"{ai_row['zastosowany produkt']} - {ai_row['status']}"):
                                st.write(f"**Status:** {ai_row['status']}")
                                st.write(f"**Uzasadnienie:** {ai_row['uzasadnienie']}")
                                if ai_row.get("debug_logs"):
                                    st.write("**Debug logi:**")
                                    for log in ai_row["debug_logs"]:
                                        st.code(log, language="text")
                    else:
                        st.dataframe(sor_registry_display_df, use_container_width=True, hide_index=True)

                    sor_registry_export_df = sor_registry_report_df.copy()
                    sor_registry_export_df = sor_registry_export_df.rename(columns={
                        "plot_name": "nr działki",
                        "product_category": "kategoria",
                        "product_name": "zastosowany produkt",
                        "treatment_date": "data",
                        "dose": "dawka",
                        "area_ha": "powierzchnia",
                        "uprawa": "uprawa",
                        "season": "sezon",
                    })
                    sor_registry_export_df = sor_registry_export_df[[col for col in ordered_columns if col in sor_registry_export_df.columns]].copy()
                    st.download_button(
                        "Eksportuj ewidencję ŚOR do XLSX",
                        data=to_excel_bytes(sor_registry_export_df),
                        file_name="ewidencja_sor.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_sor_registry_report",
                    )

        elif st.session_state.report_section == "consumption":
            st.subheader("Zużycie")
            date_columns = st.columns(2)
            with date_columns[0]:
                consumption_date_from = st.date_input(
                    "Wyświetl od", value=date.today(), key="consumption_date_from"
                )
            with date_columns[1]:
                consumption_date_to = st.date_input(
                    "Do", value=date.today(), key="consumption_date_to"
                )

            if st.button("Generuj", key="generate_consumption_report"):
                consumption_report_df = build_product_consumption_report(
                    treatments_df,
                    consumption_date_from,
                    consumption_date_to,
                )
                st.markdown("### Wyniki zużycia produktów")
                if consumption_report_df.empty:
                    st.info("Brak zużycia produktów w wybranym zakresie dat.")
                else:
                    consumption_display_df = consumption_report_df.rename(
                        columns={
                            "product_category": "kategoria",
                            "product_name": "produkt",
                            "quantity": "ilość",
                            "unit": "jednostka",
                        }
                    )
                    st.dataframe(consumption_display_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
