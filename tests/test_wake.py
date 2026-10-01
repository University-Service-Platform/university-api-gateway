"""Sleeping free-tier services: the gateway waits for them, and tells the frontend where to wake them."""
import httpx
import pytest

from app import proxy
from tests.conftest import SERVICE_URLS, bearer, build_client

RENDER_502 = httpx.Response(502, headers={"content-type": "text/html"}, text="<!DOCTYPE html><html>502</html>")


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
    monkeypatch.setattr(proxy.asyncio, "sleep", fake_sleep)
    return slept


def answers(*responses):
    """An upstream that gives these answers in turn, then echoes a 200."""
    queue = list(responses)

    def handler(request):
        if queue:
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return httpx.Response(200, json={"ok": True, "body": request.content.decode()})
    return handler


def test_waits_for_a_service_that_is_starting(platform, no_real_sleep):
    platform.override["identity"] = answers(RENDER_502, RENDER_502)
    with build_client(platform, upstream_wake_wait_seconds=40) as client:
        response = client.get("/api/v1/users", headers=bearer())
    assert response.status_code == 200
    assert len(platform.requests) == 3
    assert no_real_sleep == [2.0, 3.0]


def test_a_sign_in_is_sent_again_too(platform):
    # Render's own 502 means the request never reached the service, so resending is safe
    platform.override["identity"] = answers(RENDER_502)
    with build_client(platform, upstream_wake_wait_seconds=40) as client:
        response = client.post("/api/v1/auth/login", json={"username": "STU001", "password": "x"})
    assert response.status_code == 200
    assert response.json()["body"] == '{"username":"STU001","password":"x"}'


def test_a_service_that_was_unreachable_is_retried(platform):
    platform.override["identity"] = answers(httpx.ConnectError("refused"))
    with build_client(platform, upstream_wake_wait_seconds=40) as client:
        assert client.get("/api/v1/users", headers=bearer()).status_code == 200


def test_the_services_own_errors_pass_through_at_once(platform):
    platform.override["identity"] = answers(httpx.Response(503, json={"success": False, "error": {"code": "X"}}))
    with build_client(platform, upstream_wake_wait_seconds=40) as client:
        response = client.get("/api/v1/users", headers=bearer())
    assert response.status_code == 503 and response.json()["error"]["code"] == "X"
    assert len(platform.requests) == 1


def test_gives_up_with_a_clear_message(platform, no_real_sleep):
    platform.override["identity"] = lambda request: RENDER_502
    with build_client(platform, upstream_wake_wait_seconds=40) as client:
        response = client.get("/api/v1/users", headers=bearer())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_STARTING"
    assert sum(no_real_sleep) <= 40


def test_timeouts_are_not_resent(platform):
    platform.override["identity"] = answers(httpx.ReadTimeout("slow"))
    with build_client(platform, upstream_wake_wait_seconds=40) as client:
        assert client.get("/api/v1/users", headers=bearer()).status_code == 504
    assert len(platform.requests) == 1


def test_no_waiting_when_turned_off(platform):
    platform.override["identity"] = lambda request: RENDER_502
    with build_client(platform, upstream_wake_wait_seconds=0) as client:
        assert client.get("/api/v1/users", headers=bearer()).status_code == 503
    assert len(platform.requests) == 1


def test_wake_targets_list_public_health_addresses(platform):
    urls = {**SERVICE_URLS, "identity": "https://university-identity-service.onrender.com",
            "event": "https://event.onrender.com"}
    with build_client(platform, service_urls=urls) as client:
        targets = client.get("/gateway/wake-targets").json()["targets"]
    assert targets == [
        {"service": "identity", "url": "https://university-identity-service.onrender.com/health"},
        {"service": "event", "url": "https://event.onrender.com/actuator/health"},
    ]
