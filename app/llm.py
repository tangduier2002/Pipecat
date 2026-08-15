"""轻量 LLM 客户端 (OpenAI 兼容 chat completions)。

无外部框架 (ADR-0002): 直接 httpx 调用, 供 triage 分类与 motivation 对话复用。
未配置 LLM (缺 API key) 时调用方应回退到关键词规则路径。
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)


class LLMClient:
    """OpenAI 兼容客户端。base_url 默认指向本地 Ollama / v1。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = 10.0,
    ):
        self._base_url = (base_url or os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self._api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self._model = model or os.getenv("LLM_MODEL", "qwen2.5:7b")
        self._timeout = timeout_s

    @property
    def available(self) -> bool:
        return bool(self._api_key or "localhost" in self._base_url or "127.0.0.1" in self._base_url)

    async def chat_json(self, system: str, user: str) -> dict | None:
        """单次对话, 要求模型返回 JSON 对象。解析失败或请求异常 → None。"""
        content = await self.chat_text(system, user, temperature=0.2, response_format={"type": "json_object"})
        if content is None:
            return None
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLM 返回非 JSON, 按失败处理")
            return None

    async def chat_text(self, system: str, user: str, temperature: float = 0.7, response_format: dict | None = None) -> str | None:
        """单次对话, 返回文本。请求异常 → None。"""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.warning("LLM 调用失败: %s", exc)
            return None