"""The deployed frontend runs on a different origin than the API, so CORS has to allow it."""
import re

from starlette.middleware.cors import CORSMiddleware

from app.config import get_settings


def test_default_origins_are_the_local_frontend(env):
    assert get_settings().cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert get_settings().cors_origin_regex is None


def test_configured_origins_are_split_and_trimmed(env, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com/, https://other.example.com")
    assert get_settings().cors_origins == ("https://app.example.com", "https://other.example.com")


def test_render_deployments_allow_sibling_frontend_hosts(env, monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    regex = get_settings().cors_origin_regex
    assert regex and re.fullmatch(regex, "https://property-case-investigator-1.onrender.com")
    assert not re.fullmatch(regex, "https://evil.example.com")

    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"https://only-mine\.onrender\.com")
    assert get_settings().cors_origin_regex == r"https://only-mine\.onrender\.com"


def test_cors_middleware_is_wired_to_the_settings(env):
    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    options = cors.kwargs
    assert options["allow_credentials"] is False  # tokens travel in the Authorization header, not cookies
    assert "Authorization" in options["allow_headers"] and "PATCH" in options["allow_methods"]
    assert options["allow_origins"] or options["allow_origin_regex"]
