from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 260_000)
    return base64.b64encode(digest).decode("ascii"), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


def get_required_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


@dataclass(frozen=True)
class SessionCodec:
    secret: str
    ttl_seconds: int = 60 * 60 * 12

    def sign(self, user_id: int) -> str:
        expires = int(time.time()) + self.ttl_seconds
        nonce = secrets.token_urlsafe(12)
        payload = f"{user_id}.{expires}.{nonce}"
        sig = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def verify(self, token: str) -> int | None:
        parts = token.split(".")
        if len(parts) != 4:
            return None
        user_id_raw, expires_raw, nonce, sig = parts
        payload = f"{user_id_raw}.{expires_raw}.{nonce}"
        expected = hmac.new(self.secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        try:
            expires = int(expires_raw)
            user_id = int(user_id_raw)
        except ValueError:
            return None
        if expires < int(time.time()):
            return None
        return user_id


class CredentialCipher:
    def __init__(self, key: str):
        raw = key.strip().encode("ascii")
        self._fernet = Fernet(raw)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, value: str | None) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            return ""

