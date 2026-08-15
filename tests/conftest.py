"""共享测试替身: 记录式 fake Neo4j driver (无真实数据库环境)。"""

from __future__ import annotations


class FakeResult:
    """模拟 neo4j AsyncResult: 可 .single() 与 async 迭代 (async for)。"""

    def __init__(self, value, records: list | None = None):
        self._value = value
        self._records = records if records is not None else []

    async def single(self):
        return {"c": self._value}

    def __aiter__(self):
        self._iter = iter(self._records)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeSession:
    def __init__(self, calls: list, counts: dict | None = None, raise_on_run: bool = False):
        self._calls = calls
        self._counts = counts or {}
        self._raise = raise_on_run

    async def run(self, cypher: str, params: dict | None = None, **kwargs):
        if self._raise:
            raise ConnectionError("neo4j unreachable")
        params = params or {}
        self._calls.append((cypher, params))
        if "RETURN count(n)" in cypher:
            return FakeResult(self._counts.get("nodes", 54))
        if "RETURN count(r)" in cypher:
            return FakeResult(self._counts.get("relations", 112))
        return FakeResult(0)


class FakeDriver:
    """记录所有 run 调用的 driver; counts 决定统计查询返回值。"""

    def __init__(self, counts: dict | None = None, raise_on_run: bool = False):
        self.calls: list = []
        self._counts = counts
        self._raise = raise_on_run

    def session(self):
        return _SessionCtx(FakeSession(self.calls, self._counts, self._raise))

    async def close(self):
        pass


class _SessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False