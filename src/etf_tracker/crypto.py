from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


PBKDF2_ITERATIONS = 250_000


def encrypt_report(markdown: str, password: str) -> dict[str, object]:
    if not password:
        raise ValueError("REPORT_PASSWORD 不能为空。")

    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(password, salt)
    ciphertext = AESGCM(key).encrypt(iv, markdown.encode("utf-8"), None)
    return {
        "version": 1,
        "algorithm": "AES-GCM",
        "kdf": "PBKDF2-SHA256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": _b64(salt),
        "iv": _b64(iv),
        "ciphertext": _b64(ciphertext),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_encrypted_report(payload: dict[str, object], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")

