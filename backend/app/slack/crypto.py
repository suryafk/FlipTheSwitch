"""Encryption at rest for Slack bot tokens (Fernet: AES-128-CBC + HMAC-SHA256)."""

from cryptography.fernet import Fernet, InvalidToken

from app.slack.config import get_slack_settings


class TokenCryptoError(Exception):
    pass


def _fernet() -> Fernet:
    key = get_slack_settings().secret_key
    if not key:
        raise TokenCryptoError("BLACKBOX_SECRET_KEY is not set; refusing to store Slack tokens.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError("BLACKBOX_SECRET_KEY is not a valid Fernet key.") from exc


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("stored Slack token cannot be decrypted with this key") from exc
