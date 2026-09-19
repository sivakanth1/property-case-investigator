"""Password hashing with scrypt (salted, one-way). Plain or reversibly-encrypted passwords are never stored."""
import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

_N, _R, _P, _DKLEN = 2**14, 8, 1, 64


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = (stored or "").split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64), n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def burn_equal_time(password: str) -> None:
    """Runs a full verification against a throwaway hash so unknown emails take as long as wrong passwords."""
    verify_password(password, _dummy_hash())
