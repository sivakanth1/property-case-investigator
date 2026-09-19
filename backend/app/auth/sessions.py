import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import delete, select

from ..db import session_scope, utcnow
from ..models import AuthSession


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def user_view(profile: dict) -> dict:
    """Public account fields only; the password hash never leaves the auth module."""
    return {"id": profile["id"], "email": profile["email"], "full_name": profile.get("full_name"),
            "company": profile.get("company")}


def create_session(profile: dict, days: int) -> str:
    token = secrets.token_urlsafe(32)
    with session_scope() as s:
        s.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
        s.add(AuthSession(token_hash=_digest(token), user_id=profile["id"], email=profile["email"],
                          full_name=profile.get("full_name"), company=profile.get("company"),
                          expires_at=utcnow() + timedelta(days=days)))
    return token


def resolve_session(token: str | None) -> dict | None:
    if not token:
        return None
    with session_scope() as s:
        row = s.scalars(select(AuthSession).where(AuthSession.token_hash == _digest(token))).first()
        if row is None or row.expires_at < utcnow():
            return None
        return {"id": row.user_id, "email": row.email, "full_name": row.full_name, "company": row.company}


def revoke_session(token: str) -> None:
    with session_scope() as s:
        s.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))


class SignInThrottle:
    """At most `limit` failed sign-ins per email within `window_s`; resets on success."""

    def __init__(self, limit: int = 10, window_s: float = 900):
        self.limit, self.window_s = limit, window_s
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, email: str) -> deque:
        q = self._failures[email]
        while q and time.monotonic() - q[0] > self.window_s:
            q.popleft()
        return q

    def blocked(self, email: str) -> bool:
        with self._lock:
            return len(self._prune(email)) >= self.limit

    def fail(self, email: str) -> None:
        with self._lock:
            self._prune(email).append(time.monotonic())

    def reset(self, email: str) -> None:
        with self._lock:
            self._failures.pop(email, None)
