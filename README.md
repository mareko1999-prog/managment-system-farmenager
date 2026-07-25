# Farmenager

Prosty system do ewidencjonowania zabiegów agrotechnicznych i kosztów w Pythonie z Streamlit.

## Uruchomienie

```bash
pip install -r requirements.txt
streamlit run app.py
```

Aplikacja zapisuje dane do lokalnej bazy SQLite: `farmenager.db`.

## Logowanie (Streamlit Community Cloud)

Aplikacja używa modułu `auth.py` i biblioteki `streamlit-authenticator`.
Konfigurację logowania ustaw w `secrets.toml` lokalnie lub w sekcji **Secrets** na Streamlit Community Cloud.

Przykład:

```toml
[auth.cookie]
name = "farmenager_cookie"
key = "WSTAW_TUTAJ_DLUGI_LOSOWY_KLUCZ"
expiry_days = 30

[auth.credentials.usernames.admin]
email = "admin@example.com"
name = "Administrator"
password = "$2b$12$REPLACE_WITH_BCRYPT_HASH"
```

Uwagi:

- Pole `password` powinno zawierać hash bcrypt, nie hasło jawne.
- Dodaj kolejnych użytkowników przez następne sekcje `[auth.credentials.usernames.<login>]`.
- `auth.cookie.key` ustaw jako długi, losowy sekret.
