"""Współdzielone połączenie z bazą Turso (libSQL) używane przez auth.py i app.py.

Turso zapewnia trwałość danych między restartami/redeployami na Streamlit
Community Cloud (lokalny filesystem kontenera jest efemeryczny). Używamy trybu
"embedded replica": lokalny plik `farmenager_replica.db` służy do szybkich
odczytów, a zapisy są przekazywane do zdalnej bazy Turso i odbijane z powrotem
do repliki.

Wymagana konfiguracja w `.streamlit/secrets.toml` (lub w sekcji Secrets na
Streamlit Community Cloud):

    [turso]
    url = "libsql://twoja-baza-xxxx.turso.io"
    auth_token = "twoj-token"
"""

import os
import sqlite3
import threading
from typing import Any

import streamlit as st

try:
    import libsql
except ImportError:  # pragma: no cover - biblioteka może nie być jeszcze zainstalowana
    libsql = None

LOCAL_REPLICA_PATH = os.path.join(os.path.dirname(__file__), "farmenager_replica.db")
LOCAL_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "farmenager.db")

# libSQL/SQLite nie gwarantuje bezpiecznego dostępu z wielu wątków jednocześnie,
# a Streamlit obsługuje równoległe sesje użytkowników we współdzielonym procesie.
# Blokada serializuje dostęp do jedynego, współdzielonego połączenia.
# RLock pozwala funkcjom zapisu wykonać zagnieżdżony odczyt na tym samym wątku.
_connection_lock = threading.RLock()


_WRITE_OPS = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE")


class _ConnectionHandle:
    """Cienki wrapper, dzięki któremu `with get_connection() as conn:` działa
    dokładnie tak jak wcześniej z `sqlite3.Connection`.

    Commit jest wywoływany w __exit__ **tylko** gdy w bloku wystąpiły operacje
    zapisu (INSERT/UPDATE/DELETE/…). Odczyty (SELECT) nie generują zbędnego
    commit() do Turso.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._dirty = False

    def __enter__(self) -> "Any":
        _connection_lock.acquire()
        self._dirty = False
        return self

    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        stripped = sql.strip().upper()
        if any(stripped.startswith(op) for op in _WRITE_OPS):
            self._dirty = True
        return self._conn.execute(sql, parameters)

    def commit(self) -> None:
        """Jawny commit – od razu przesyła zapis do Turso."""
        self._conn.commit()
        self._dirty = False

    def rollback(self) -> None:
        """Wycofuje zapisane w bieżącej transakcji zmiany."""
        self._conn.rollback()
        self._dirty = False

    def fetchone(self) -> Any:
        return self._conn.fetchone()

    def fetchall(self) -> Any:
        return self._conn.fetchall()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        try:
            if exc_type is None and self._dirty:
                # Commituj tylko jeśli były zapisy i nie zrobiono tego jawnie
                self._conn.commit()
                self._dirty = False
        finally:
            _connection_lock.release()
        return False


def _looks_like_placeholder(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return any(
        marker in normalized
        for marker in ("twoja-baza", "xxxx.turso.io", "wklej-tutaj", "twoj-token", "replace")
    )


def _get_turso_secrets() -> dict[str, str] | None:
    turso_config = st.secrets.get("turso", {})
    url = str(turso_config.get("url") or "").strip()
    auth_token = str(turso_config.get("auth_token") or "").strip()
    if not url or not auth_token:
        return None
    if _looks_like_placeholder(url) or _looks_like_placeholder(auth_token):
        return None
    return {"url": url, "auth_token": auth_token}


def _connect_local_sqlite() -> Any:
    return sqlite3.connect(LOCAL_SQLITE_PATH, check_same_thread=False)


@st.cache_resource(show_spinner="Łączenie z bazą danych...")
def _shared_connection() -> Any:
    secrets = _get_turso_secrets()
    if libsql is not None and secrets is not None:
        try:
            conn = libsql.connect(
                LOCAL_REPLICA_PATH,
                sync_url=secrets["url"],
                auth_token=secrets["auth_token"],
                sync_interval=300,
            )
            conn.sync()
            return conn
        except Exception as exc:
            st.warning(f"Nie udało się połączyć z Turso ({exc}). Przełączono na lokalną bazę SQLite.")

    return _connect_local_sqlite()


def get_connection() -> _ConnectionHandle:
    """Zwraca menedżera kontekstu do współdzielonego połączenia z Turso.

    Sposób użycia jest identyczny jak wcześniej z bazą sqlite3:

        with get_connection() as conn:
            conn.execute(...)
            conn.commit()
    """
    return _ConnectionHandle(_shared_connection())
