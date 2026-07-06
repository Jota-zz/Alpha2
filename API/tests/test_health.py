"""Test del endpoint de health (router en aislamiento, sin arrancar el bot)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app)


def test_health_ok():
    resp = _client().get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
