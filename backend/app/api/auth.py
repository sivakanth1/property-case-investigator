from fastapi import APIRouter, Request

from ..auth.passwords import burn_equal_time, hash_password, verify_password
from ..auth.sessions import SignInThrottle, create_session, resolve_session, revoke_session, user_view
from ..config import get_settings
from ..data.supabase_admin import EmailTaken, SupabaseAdmin, SupabaseError
from ..schemas import SavePropertyRequest, SignInRequest, SignUpRequest
from .errors import api_error

router = APIRouter(prefix="/api", tags=["accounts"])
throttle = SignInThrottle()

INVALID = "Email or password is incorrect."


def _admin(request: Request) -> SupabaseAdmin:
    admin = getattr(request.app.state, "supabase", None)
    if admin is None:
        raise api_error(503, "accounts_not_configured", "Accounts are not configured: add SUPABASE_URL and "
                        "SUPABASE_SERVICE_ROLE_KEY to backend/.env and restart the backend.")
    return admin


def _token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


def current_user(request: Request) -> dict:
    user = resolve_session(_token(request))
    if user is None:
        raise api_error(401, "not_signed_in", "Your session has expired. Sign in again.")
    return user


def optional_user(request: Request) -> dict | None:
    """None for guests (no token); 401 if a token was sent but is no longer valid."""
    return current_user(request) if _token(request) else None


def _normalize(email: str) -> str:
    return email.strip().lower()


def _clean(value: str | None) -> str | None:
    return value.strip() or None if value else None


@router.post("/auth/signup", status_code=201)
def sign_up(body: SignUpRequest, request: Request):
    admin = _admin(request)
    email = _normalize(body.email)
    try:
        existing = admin.get_profile_by_email(email)
        if existing and existing.get("password_hash"):
            raise api_error(409, "email_taken", "An account with this email already exists. Sign in instead.")
        password_hash = hash_password(body.password)
        if existing:  # profile row from the old Supabase-Auth setup: attach a password to it
            profile = admin.set_password(existing["id"], password_hash, _clean(body.full_name), _clean(body.company))
        else:
            profile = admin.create_profile(email, _clean(body.full_name) or email.split("@")[0], _clean(body.company),
                                           password_hash)
    except EmailTaken:
        raise api_error(409, "email_taken", "An account with this email already exists. Sign in instead.")
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))
    return {"token": create_session(profile, get_settings().session_days), "user": user_view(profile)}


@router.post("/auth/signin")
def sign_in(body: SignInRequest, request: Request):
    admin = _admin(request)
    email = _normalize(body.email)
    if throttle.blocked(email):
        raise api_error(429, "too_many_attempts", "Too many failed sign-ins for this email. Try again in 15 minutes.")
    try:
        profile = admin.get_profile_by_email(email)
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))
    if not profile or not profile.get("password_hash"):
        burn_equal_time(body.password)
        throttle.fail(email)
        raise api_error(401, "invalid_credentials", INVALID)
    if not verify_password(body.password, profile["password_hash"]):
        throttle.fail(email)
        raise api_error(401, "invalid_credentials", INVALID)
    throttle.reset(email)
    return {"token": create_session(profile, get_settings().session_days), "user": user_view(profile)}


@router.get("/auth/me")
def me(request: Request):
    return current_user(request)


@router.post("/auth/signout", status_code=204)
def sign_out(request: Request):
    token = _token(request)
    if token:
        revoke_session(token)


@router.get("/portfolio")
def portfolio(request: Request):
    user = current_user(request)
    try:
        return _admin(request).list_saved(user["id"])
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))


@router.post("/portfolio", status_code=201)
def save_to_portfolio(body: SavePropertyRequest, request: Request):
    user = current_user(request)
    try:
        return _admin(request).save_property(user["id"], body.hcad, body.address.strip(), _clean(body.zip))
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))


@router.delete("/portfolio/{hcad}", status_code=204)
def remove_from_portfolio(hcad: str, request: Request):
    user = current_user(request)
    try:
        _admin(request).delete_saved(user["id"], hcad)
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))
