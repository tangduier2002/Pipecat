"""T1 /health 端点测试: 已连接 / 未连接 / 未初始化三态。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import FakeDriver


def test_health_connected():
    # TestClient 的 lifespan 会初始化真实 driver, 进入 with 后再注入 fake
    with TestClient(app) as client:
        app.state.driver = FakeDriver()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "neo4j": "connected"}


def test_health_disconnected():
    with TestClient(app) as client:
        app.state.driver = FakeDriver(raise_on_run=True)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "degraded", "neo4j": "disconnected"}


def test_health_uninitialized():
    with TestClient(app) as client:
        app.state.driver = None
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "degraded", "neo4j": "uninitialized"}