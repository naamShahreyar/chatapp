def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_signup_returns_user_without_password(client):
    r = client.post("/auth/signup", json={"username": "alice", "password": "password123"})
    assert r.status_code == 201
    assert r.json()["username"] == "alice"
    assert "password" not in r.text


def test_signup_duplicate_username(client):
    payload = {"username": "alice", "password": "password123"}
    client.post("/auth/signup", json=payload)
    assert client.post("/auth/signup", json=payload).status_code == 409


def test_signup_rejects_short_password(client):
    r = client.post("/auth/signup", json={"username": "alice", "password": "short"})
    assert r.status_code == 422


def test_login_wrong_password(client):
    client.post("/auth/signup", json={"username": "alice", "password": "password123"})
    r = client.post("/auth/login", data={"username": "alice", "password": "wrongpassword"})
    assert r.status_code == 401


def test_me_requires_token(client):
    assert client.get("/auth/me").status_code == 401


def test_me_with_token(client, login):
    r = client.get("/auth/me", headers=login())
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
