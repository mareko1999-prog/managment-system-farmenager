import streamlit as st
import libsql_experimental as libsql
import time
import os

st.title("Sieciowy Benchmark: Streamlit (USA) <-> Turso (USA)")

# Pobieranie danych logowania z secrets (upewnij się, że masz je ustawione)
URL = st.secrets.get("TURSO_DATABASE_URL")
TOKEN = st.secrets.get("TURSO_AUTH_TOKEN")

if not URL or not TOKEN:
    st.error("Brak danych dostępowych w st.secrets!")
    st.stop()

if st.button("Uruchom testy opóźnień", type="primary"):
    results = []
    
    with st.spinner("Wykonuję testy..."):
        # 1. Test: sync() przy starcie
        start_time = time.perf_counter()
        conn = libsql.connect("file:replica_test.db", sync_url=URL, auth_token=TOKEN)
        conn.sync()
        sync_time = (time.perf_counter() - start_time) * 1000
        results.append({"Operacja": "sync() przy starcie", "Czas [ms]": f"{sync_time:.2f}"})

        # Przygotowanie tabeli testowej
        conn.execute("CREATE TABLE IF NOT EXISTS ping_test (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
        conn.commit()

        # 2. Test: SELECT 1 (20 powtórzeń)
        select1_times = []
        for _ in range(20):
            t0 = time.perf_counter()
            conn.execute("SELECT 1").fetchall()
            select1_times.append((time.perf_counter() - t0) * 1000)
        avg_select1 = sum(select1_times) / len(select1_times)
        results.append({"Operacja": "SELECT 1 (20 powtórzeń avg)", "Czas [ms]": f"{avg_select1:.3f}"})

        # 3. Test: SELECT * FROM (10 powtórzeń)
        select_all_times = []
        for _ in range(10):
            t0 = time.perf_counter()
            conn.execute("SELECT * FROM ping_test").fetchall()
            select_all_times.append((time.perf_counter() - t0) * 1000)
        avg_select_all = sum(select_all_times) / len(select_all_times)
        results.append({"Operacja": "SELECT * FROM fields (10x avg)", "Czas [ms]": f"{avg_select_all:.3f}"})

        # 4. Test: INSERT + commit (5 powtórzeń osobno)
        insert_times = []
        for i in range(5):
            t0 = time.perf_counter()
            conn.execute(f"INSERT INTO ping_test (name) VALUES ('pojedynczy_test_{i}')")
            conn.commit()
            insert_times.append((time.perf_counter() - t0) * 1000)
        avg_insert = sum(insert_times) / len(insert_times)
        results.append({"Operacja": "INSERT + commit (5x osobno avg)", "Czas [ms]": f"{avg_insert:.2f}"})

        # 5. Test: 5x INSERT + 1 commit (paczka)
        start_time = time.perf_counter()
        for i in range(5):
            conn.execute(f"INSERT INTO ping_test (name) VALUES ('paczka_test_{i}')")
        conn.commit()
        bulk_time = (time.perf_counter() - start_time) * 1000
        results.append({"Operacja": "5x INSERT + 1 commit", "Czas [ms]": f"{bulk_time:.2f}"})

        # Sprzątanie bazy po testach
        conn.execute("DROP TABLE ping_test")
        conn.commit()

    st.success("Testy zakończone!")
    st.table(results)