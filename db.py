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
import threading
from typing import Any

import streamlit as st

try:
    import libsql
except ImportError:  # pragma: no cover - biblioteka może nie być jeszcze zainstalowana
    libsql = None

LOCAL_REPLICA_PATH = os.path.join(os.path.dirname(__file__), "farmenager_replica.db")

# libSQL/SQLite nie gwarantuje bezpiecznego dostępu z wielu wątków jednocześnie,
# a Streamlit obsługuje równoległe sesje użytkowników we współdzielonym procesie.
# Blokada serializuje dostęp do jedynego, współdzielonego połączenia.
_connection_lock = threading.Lock()


class _ConnectionHandle:
    """Cienki wrapper, dzięki któremu `with get_connection() as conn:` działa
    dokładnie tak jak wcześniej z `sqlite3.Connection` (commit przy sukcesie,
    bez zamykania współdzielonego połączenia)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __enter__(self) -> Any:
        _connection_lock.acquire()
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._conn.commit()
        finally:
            _connection_lock.release()
        return False


def _get_turso_secrets() -> dict[str, str]:
    turso_config = st.secrets.get("turso", {})
    url = str(turso_config.get("url") or "").strip()
    auth_token = str(turso_config.get("auth_token") or "").strip()
    if not url or not auth_token:
        st.error(
            "Brak konfiguracji Turso. Ustaw sekcję [turso] (url, auth_token) "
            "w secrets.toml przed uruchomieniem aplikacji."
        )
        st.stop()
    return {"url": url, "auth_token": auth_token}


@st.cache_resource(show_spinner="Łączenie z bazą danych...")
def _shared_connection() -> Any:
    if libsql is None:
        st.error(
            "Brak biblioteki 'libsql'. Dodaj ją do requirements.txt "
            "(pip install libsql) i zainstaluj ponownie zależności."
        )
        st.stop()

    secrets = _get_turso_secrets()
    conn = libsql.connect(
        LOCAL_REPLICA_PATH,
        sync_url=secrets["url"],
        auth_token=secrets["auth_token"],
        sync_interval=30,
    )
    conn.sync()
    return conn


def get_connection() -> _ConnectionHandle:
    """Zwraca menedżera kontekstu do współdzielonego połączenia z Turso.

    Sposób użycia jest identyczny jak wcześniej z bazą sqlite3:

        with get_connection() as conn:
            conn.execute(...)
            conn.commit()
    """
    return _ConnectionHandle(_shared_connection())
