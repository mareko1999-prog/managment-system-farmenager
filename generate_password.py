"""
Skrypt pomocniczy do generowania haseł dla streamlit-authenticator.
Użycie: python generate_password.py
"""
import bcrypt
import secrets


def generate_cookie_key():
    """Generuje losowy klucz do szyfrowania ciasteczek."""
    return secrets.token_urlsafe(32)


def generate_password_hash(password: str) -> str:
    """Generuje hash bcrypt dla podanego hasła."""
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode()


def main():
    print("=" * 60)
    print("Generator konfiguracji dla streamlit-authenticator")
    print("=" * 60)
    
    # Generowanie klucza cookie
    print("\n1. KLUCZ COOKIE (zapisz w [auth.cookie] -> key):")
    print("-" * 60)
    cookie_key = generate_cookie_key()
    print(f"key = \"{cookie_key}\"")
    
    # Generowanie haseł
    print("\n2. HASŁA UŻYTKOWNIKÓW:")
    print("-" * 60)
    print("Wprowadź dane użytkowników (wciśnij Enter bez loginu aby zakończyć)\n")
    
    users = []
    while True:
        username = input("Login użytkownika (lub Enter aby zakończyć): ").strip()
        if not username:
            break
        
        name = input(f"Pełna nazwa dla '{username}': ").strip() or username
        password = input(f"Hasło dla '{username}': ").strip()
        
        if not password:
            print("  ⚠️  Hasło nie może być puste, pomijam użytkownika.")
            continue
        
        password_hash = generate_password_hash(password)
        users.append((username, name, password_hash))
        print(f"  ✓ Użytkownik '{username}' dodany.\n")
    
    if users:
        print("\n3. KONFIGURACJA DO SKOPIOWANIA DO secrets.toml:")
        print("=" * 60)
        
        for username, name, password_hash in users:
            print(f"\n[auth.credentials.usernames.{username}]")
            print(f"name = \"{name}\"")
            print(f"password = \"{password_hash}\"")
        
        print("\n[auth.cookie]")
        print(f"key = \"{cookie_key}\"")
        print("name = \"farmenager_auth\"")
        print("expiry_days = 30")
        
        print("\n" + "=" * 60)
        print("Skopiuj powyższą konfigurację do pliku .streamlit/secrets.toml")
        print("=" * 60)
    else:
        print("\nBrak użytkowników do wygenerowania.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nPrzerwano.")
    except Exception as e:
        print(f"\n❌ Błąd: {e}")
