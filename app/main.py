"""FastAPI 应用入口。

生命周期管理 Neo4j 异步驱动 (app.state.driver), 供后续组件 (kg_service /
memory) 复用同一连接实例。T1 提供 /health 健康检查。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from neo4j import AsyncGraphDatabase

from app.config import settings
from app.memory import patient_summary

logger = logging.getLogger(__name__)

_HEALTH_TIMEOUT_S = 3.0

# 会话状态: 进程内存缓存, 启动时从 Patient KG 恢复 (T4)
_session_cache: dict = {}


def _warn_smtp_misconfig() -> None:
    """启动时校验 SMTP 配置; 缺失仅告警, 不阻塞启动。"""
    if not settings.smtp_host:
        logger.warning("SMTP_HOST 未配置, 危机邮件通知将跳过 (guard 降级语义)")
        return
    if not (settings.smtp_user and settings.smtp_password):
        logger.warning("SMTP_USER/SMTP_PASSWORD 未配置, 真实 SMTP 会拒绝匿名发送")


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
    _warn_smtp_misconfig()
    # 会话状态恢复: 默认患者概要加载到进程内存 (失败不阻塞启动)
    try:
        _session_cache["default"] = await patient_summary("default", driver)
    except Exception:
        _session_cache["default"] = {}
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


@app.get("/memory/{patient_id}")
async def memory_snapshot(patient_id: str) -> dict:
    """记忆快照 (T4): 优先进程缓存, 未命中时查询 Patient KG 并缓存。"""
    driver = getattr(app.state, "driver", None)
    if driver is None:
        return {"patient_id": patient_id, "error": "neo4j uninitialized"}
    cache_key = f"patient:{patient_id}"
    if cache_key not in _session_cache:
        try:
            _session_cache[cache_key] = await patient_summary(patient_id, driver)
        except Exception as exc:
            return {"patient_id": patient_id, "error": str(exc)}
    return _session_cache[cache_key]