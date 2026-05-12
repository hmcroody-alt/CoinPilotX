from werkzeug.security import check_password_hash, generate_password_hash


def normalize_email(email):
    return (email or "").strip().lower()


def hash_password(password):
    return generate_password_hash(password or "")


def verify_password(password_hash, password):
    if not password_hash:
        return False
    return check_password_hash(password_hash, password or "")


def password_is_strong(password):
    password = password or ""
    return {
        "ok": len(password) >= 12
        and any(char.isupper() for char in password)
        and any(char.islower() for char in password)
        and any(char.isdigit() for char in password)
        and any(not char.isalnum() for char in password),
        "min_length": len(password) >= 12,
        "uppercase": any(char.isupper() for char in password),
        "lowercase": any(char.islower() for char in password),
        "number": any(char.isdigit() for char in password),
        "symbol": any(not char.isalnum() for char in password),
    }
