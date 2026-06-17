import hashlib
import hmac
import json
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from outreach.config import get_settings


class CryptoError(RuntimeError):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().secret_key.strip()
    if not key:
        raise CryptoError("SECRET_KEY missing in environment")
    try:
        return Fernet(key.encode())
    except Exception as e:  # noqa: BLE001
        raise CryptoError(f"SECRET_KEY is not a valid Fernet key: {e}") from e


def encrypt_json(payload: dict) -> bytes:
    return _fernet().encrypt(json.dumps(payload, separators=(",", ":")).encode())


def decrypt_json(blob: bytes) -> dict:
    try:
        return json.loads(_fernet().decrypt(blob).decode())
    except InvalidToken as e:
        raise CryptoError("ciphertext could not be decrypted (key rotated?)") from e


def mint_extension_token() -> str:
    """48 random bytes -> base64url-ish string, ~64 chars. Shown ONCE to the user."""
    return secrets.token_urlsafe(48)


def hmac_token(raw: str) -> str:
    """Stable hex HMAC of an extension token, keyed by TOKEN_SECRET. Stored at rest."""
    settings = get_settings()
    return hmac.new(
        settings.token_secret.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
