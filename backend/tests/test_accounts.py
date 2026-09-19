import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password, verify_password
from app.data.supabase_admin import EmailTaken, SupabaseAdmin, SupabaseError
from app.db import dispose_engine, init_engine


class FakeSupabase:
    """In-memory stand-in for the Supabase profiles / saved_properties tables (TEST FIXTURE)."""

    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.saved: list[dict] = []

    def get_profile_by_email(self, email):
        return next((dict(p) for p in self.profiles.values() if p["email"] == email), None)

    def create_profile(self, email, full_name, company, password_hash):
        if self.get_profile_by_email(email):
            raise EmailTaken("taken")
        pid = f"user-{len(self.profiles) + 1}"
        self.profiles[pid] = {"id": pid, "email": email, "full_name": full_name, "company": company,
                              "password_hash": password_hash}
        return dict(self.profiles[pid])

    def set_password(self, profile_id, password_hash, full_name, company):
        p = self.profiles[profile_id]
        p["password_hash"] = password_hash
        p["full_name"] = full_name or p["full_name"]
        return dict(p)

    def list_saved(self, user_id):
        return [s for s in self.saved if s["user_id"] == user_id]

    def save_property(self, user_id, hcad, address, zip_code):
        self.saved = [s for s in self.saved if not (s["user_id"] == user_id and s["hcad"] == hcad)]
        row = {"id": str(len(self.saved) + 1), "user_id": user_id, "hcad": hcad, "address": address, "zip": zip_code,
               "created_at": "2026-09-19T00:00:00Z"}
        self.saved.append(row)
        return row

    def delete_saved(self, user_id, hcad):
        self.saved = [s for s in self.saved if not (s["user_id"] == user_id and s["hcad"] == hcad)]

    def close(self):
        pass


@pytest.fixture
def client(env):
    from app.api import auth as auth_api
    from app.main import app

    auth_api.throttle.reset("owner@example.com")
    fake = FakeSupabase()
    with TestClient(app) as c:
        c.app.state.supabase = fake
        c.fake = fake
        yield c


def signup(c, email="Owner@Example.com", password="correct horse 1"):
    return c.post("/api/auth/signup", json={"email": email, "password": password, "full_name": "Pat Owner", "company": "Acme"})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_passwords_are_salted_one_way_hashes():
    h1, h2 = hash_password("s3cret-pass"), hash_password("s3cret-pass")
    assert h1 != h2 and h1.startswith("scrypt$") and "s3cret-pass" not in h1
    assert verify_password("s3cret-pass", h1) and not verify_password("s3cret-pasS", h1)
    assert not verify_password("anything", "not-a-hash") and not verify_password("anything", None)


def test_signup_signin_me_and_signout(client):
    r = signup(client)
    assert r.status_code == 201
    body = r.json()
    assert body["user"] == {"id": "user-1", "email": "owner@example.com", "full_name": "Pat Owner", "company": "Acme"}
    assert "password" not in json.dumps(body)
    stored = client.fake.profiles["user-1"]["password_hash"]
    assert stored.startswith("scrypt$") and "correct horse 1" not in stored

    assert client.get("/api/auth/me", headers=bearer(body["token"])).json()["email"] == "owner@example.com"
    wrong = client.post("/api/auth/signin", json={"email": "owner@example.com", "password": "nope"})
    assert wrong.status_code == 401 and wrong.json()["error"]["message"] == "Email or password is incorrect."
    unknown = client.post("/api/auth/signin", json={"email": "nobody@example.com", "password": "nope"})
    assert unknown.status_code == 401 and unknown.json()["error"]["message"] == wrong.json()["error"]["message"]

    ok = client.post("/api/auth/signin", json={"email": " OWNER@example.com ", "password": "correct horse 1"})
    assert ok.status_code == 200 and ok.json()["user"]["id"] == "user-1"
    assert client.post("/api/auth/signout", headers=bearer(ok.json()["token"])).status_code == 204
    assert client.get("/api/auth/me", headers=bearer(ok.json()["token"])).status_code == 401
    assert client.get("/api/auth/me").status_code == 401

    assert signup(client).status_code == 409
    assert signup(client, email="not-an-email").status_code == 422
    assert signup(client, email="new@example.com", password="short").status_code == 422


def test_session_survives_backend_restart(client):
    token = signup(client).json()["token"]
    dispose_engine()
    init_engine(os.environ["DATABASE_URL"])
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 200


def test_portfolio_is_scoped_to_the_signed_in_user(client):
    a = signup(client).json()["token"]
    b = signup(client, email="other@example.com").json()["token"]
    item = {"hcad": "0551730000009", "address": "1801 SAKOWITZ", "zip": "77020"}
    assert client.post("/api/portfolio", json=item, headers=bearer(a)).status_code == 201
    assert [p["hcad"] for p in client.get("/api/portfolio", headers=bearer(a)).json()] == ["0551730000009"]
    assert client.get("/api/portfolio", headers=bearer(b)).json() == []
    assert client.post("/api/portfolio", json=item).status_code == 401
    assert client.delete("/api/portfolio/0551730000009", headers=bearer(a)).status_code == 204
    assert client.get("/api/portfolio", headers=bearer(a)).json() == []


def test_repeated_failures_are_throttled(client):
    signup(client)
    for _ in range(10):
        assert client.post("/api/auth/signin", json={"email": "owner@example.com", "password": "bad"}).status_code == 401
    blocked = client.post("/api/auth/signin", json={"email": "owner@example.com", "password": "correct horse 1"})
    assert blocked.status_code == 429


def test_old_supabase_auth_profile_gets_a_password_on_signup(client):
    client.fake.profiles["legacy"] = {"id": "legacy", "email": "owner@example.com", "full_name": None, "company": None,
                                      "password_hash": None}
    assert client.post("/api/auth/signin", json={"email": "owner@example.com", "password": "x"}).status_code == 401
    r = signup(client)
    assert r.status_code == 201 and r.json()["user"]["id"] == "legacy"
    assert client.post("/api/auth/signin", json={"email": "owner@example.com", "password": "correct horse 1"}).status_code == 200


def test_accounts_not_configured(env):
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/health").json()["accounts"]["configured"] is False
        r = signup(c)
    assert r.status_code == 503 and "SUPABASE_SERVICE_ROLE_KEY" in r.json()["error"]["message"]


def test_supabase_admin_rest_calls():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(409, json={"code": "23505", "message": "duplicate key"})
        if "fail" in str(request.url):
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=[{"id": "u1", "email": "a@b.co", "password_hash": "scrypt$x"}])

    admin = SupabaseAdmin("https://proj.supabase.co/", "sb_secret_testkey", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert admin.get_profile_by_email("a+b@b.co")["id"] == "u1"
    req = seen[-1]
    assert req.url.path == "/rest/v1/profiles" and req.url.params["email"] == "eq.a+b@b.co"
    assert req.headers["apikey"] == "sb_secret_testkey" and "authorization" not in req.headers
    with pytest.raises(EmailTaken):
        admin.create_profile("a@b.co", "A", None, "scrypt$x")
    with pytest.raises(SupabaseError):
        admin.list_saved("fail")

    legacy = SupabaseAdmin("https://proj.supabase.co", "eyJlegacy", http=httpx.Client(transport=httpx.MockTransport(handler)))
    legacy.list_saved("u1")
    assert seen[-1].headers["authorization"] == "Bearer eyJlegacy"
