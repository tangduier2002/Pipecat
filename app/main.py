"""FastAPI 应用入口。

生命周期管理 Neo4j 异步驱动 (app.state.driver), 供后续组件 (kg_service /
memory) 复用同一连接实例。T1 提供 /health 健康检查。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from neo4j import AsyncGraphDatabase

from app.config import settings

_HEALTH_TIMEOUT_S = 3.0


async def _neo4j_connected(driver) -> bool:
    """探测 Neo4j 连通性 (3 秒超时)。"""
    try:
        async with asyncio.timeout(_HEALTH_TIMEOUT_S):
            async with driver.session() as session:
                await session.run("RETURN 1")
        return True
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    app.state.driver = driver
    yield
    await driver.close()


app = FastAPI(title="Pipecat CDSMP 健康教练", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    driver = getattr(app.state, "driver", None)
    if driver is None:
        return {"status": "degraded", "neo4j": "uninitialized"}
    if await _neo4j_connected(driver):
        return {"status": "ok", "neo4j": "connected"}
    return {"status": "degraded", "neo4j": "disconnected"}