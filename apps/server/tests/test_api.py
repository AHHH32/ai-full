from fastapi.testclient import TestClient
from app.main import app, sessions, created


def test_start_and_signalling_are_scoped_to_session_secret(capsys):
    sessions.clear()
    created.clear()
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"]
        start = client.post("/api/start", json={"mode": "mock"}).json()
        endpoint = start["webrtc_request_params"]["endpoint"]
        assert client.post(endpoint, json={"sdp": "invalid", "type": "offer"}).status_code == 404
        key = start["webrtc_request_params"]["headers"]
        assert client.patch(endpoint, headers=key, json={"pc_id": "another", "candidates": []}).status_code == 400
        assert client.post(endpoint, headers=key, json={"pc_id": "another", "sdp": "invalid", "type": "offer"}).status_code == 400
        assert client.post("/api/start", json={"mode": "invalid"}).status_code == 422
    output = capsys.readouterr().err
    assert "返回 400" in output and "返回 422" in output
    assert key["X-Session-Key"] not in output
