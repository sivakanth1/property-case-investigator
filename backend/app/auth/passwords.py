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
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2$100000${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        parts = (stored or "").split("$")
        algo = parts[0]
        
        if algo == "scrypt":
            # Support legacy scrypt hashes if any
            _, n, r, p, salt_b64, hash_b64 = parts
            expected = base64.b64decode(hash_b64)
            digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt_b64), n=int(n), r=int(r),
                                    p=int(p), dklen=len(expected))
            return hmac.compare_digest(digest, expected)
            
        elif algo == "pbkdf2":
            _, iterations, salt_b64, hash_b64 = parts
            expected = base64.b64decode(hash_b64)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iterations))
            return hmac.compare_digest(digest, expected)
            
        else:
            return False
            
    except (ValueError, TypeError, AttributeError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def burn_equal_time(password: str) -> None:
    """Runs a full verification against a throwaway hash so unknown emails take as long as wrong passwords."""
    verify_password(password, _dummy_hash())
