import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from app.core.config import settings

# Password hashing instance (Argon2)
password_hash = PasswordHash((Argon2Hasher(),))
_jwt_secret_key = ""


def set_jwt_secret(secret: str) -> None:
    global _jwt_secret_key
    _jwt_secret_key = secret

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)

def _get_secret_key() -> str:
    if not _jwt_secret_key:
        raise RuntimeError("JWT secret has not been loaded from AWS Secrets Manager")
    return _jwt_secret_key

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, _get_secret_key(), algorithm=settings.ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, _get_secret_key(), algorithms=[settings.ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None
