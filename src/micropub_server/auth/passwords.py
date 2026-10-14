"""Password hashing: argon2id, with bcrypt accepted for the Indiekit cut-over."""

from __future__ import annotations

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, *, password_hash: str = "", password_secret: str = "") -> bool:
    if password_hash:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
    if password_secret:
        try:
            return bcrypt.checkpw(password.encode(), password_secret.encode())
        except ValueError:
            return False
    return False
