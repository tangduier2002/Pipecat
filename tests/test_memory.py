"""T4 memory 测试: 四步管道 + 校验拦截 + 危机预标记 + 幂等合并。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.memory import (
    Node,
    PatientRecord,
    RecordItem,
    extract,
    items_to_nodes,
    memory_pipeline,
    merge,
    patient_summary,
    validate,
    write,
)
from tests.conftest import FakeDriver


class FakeLLM:
    def __init__(self, response: dict | None = None, fail: bool = False):
        self._response = response or {"records": []}
        self._fail = fail

    @property
    def available(self) -> bool:
        return True

    async def chat_json(self, system: str, user: str) -> dict | None:
        if self._fail:
            return None
        return self._response


def _bp_record(systolic: float, diastolic: float | None = None, ts: str | None = None) -> PatientRecord:
    ts = ts or (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return PatientRecord(
        records=[
            RecordItem(type="vitalsign", name="血压", value=systolic, unit="mmHg", diastolic=diastolic, timestamp=ts)
        ]
    )


async def test_extract_bp_pair_from_text():
    llm = FakeLLM(
        {
            "records": [
                {"type": "vitalsign", "name": "血压", "value": 155, "diastolic": 95, "unit": "mmHg", "timestamp": "2026-08-16T08:00:00+00:00"}
            ]
        }
    )
    record = await extract("今天早上量血压 155/95", llm)
    assert len(record.records) == 1
    assert record.records[0].value == 155
    assert record.records[0].diastolic == 95


async def test_extract_drops_drug_records():
    llm = FakeLLM(
        {
            "records": [
                {"type": "vitalsign", "name": "血压", "value": 140, "timestamp": "2026-08-16T08:00:00+00:00"},
                {"type": "lifeevent", "name": "吃降压药", "description": "加了一片", "timestamp": "2026-08-16T08:00:00+00:00"},
            ]
        }
    )
    record = await extract("今天量了血压还吃了药", llm)
    assert len(record.records) == 1
    assert record.records[0].name == "血压"


async def test_extract_llm_failure_returns_empty():
    record = await extract("今天早上量血压 155/95", FakeLLM(fail=True))
    assert record.records == []


async def test_extract_ignores_unknown_types():
    llm = FakeLLM({"records": [{"type": "medication", "name": "硝苯地平"}]})
    record = await extract("在吃硝苯地平", llm)
    assert record.records == []


def test_validate_normal_bp_passes():
    results = validate(_bp_record(155, 95))
    assert results[0].ok is True
    assert results[0].crisis_pending is False


def test_validate_bp_300_blocked():
    results = validate(_bp_record(300, 95))
    assert results[0].ok is False
    assert "合理范围" in results[0].reason


def test_validate_systolic_185_crisis_pending():
    results = validate(_bp_record(185, 95))
    assert results[0].ok is True
    assert results[0].crisis_pending is True


def test_validate_diastolic_120_crisis_pending():
    results = validate(_bp_record(150, 120))
    assert results[0].ok is True
    assert results[0].crisis_pending is True


def test_validate_out_of_range_diastolic_blocked():
    results = validate(_bp_record(150, 170))
    assert results[0].ok is False


def test_validate_missing_timestamp_defaults_now():
    results = validate(PatientRecord(records=[RecordItem(type="emotion", name="焦虑", timestamp="")]))
    assert results[0].item.timestamp  # 已补默认


def test_validate_future_timestamp_clamped():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    results = validate(PatientRecord(records=[RecordItem(type="emotion", name="焦虑", timestamp=future)]))
    assert results[0].item.timestamp != future


def test_merge_same_day_bp_keeps_latest():
    today = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    existing = [
        Node(
            label="VitalSign",
            props={"name": "血压", "value": 120, "unit": "mmHg", "timestamp": today, "date": today[:10]},
            relation="测量_AT",
            merge_key=f"VitalSign|血压|{today[:10]}",
        )
    ]
    merged = merge(existing, _bp_record(155, 95))
    assert len(merged) == 1
    assert merged[0].props["value"] == 155
    assert merged[0].props["history"] == [120]


def test_merge_append_only_types_keep_all():
    existing: list[Node] = []
    record = PatientRecord(
        records=[
            RecordItem(type="emotion", name="焦虑", timestamp="2026-08-16T08:00:00+00:00"),
            RecordItem(type="emotion", name="焦虑", timestamp="2026-08-17T08:00:00+00:00"),
        ]
    )
    merged = merge(existing, record)
    assert len(merged) == 2  # 时序追加, 不合并


def test_merge_different_days_no_collapse():
    existing: list[Node] = []
    record = PatientRecord(
        records=[
            RecordItem(type="vitalsign", name="血压", value=120, timestamp="2026-08-16T08:00:00+00:00"),
            RecordItem(type="vitalsign", name="血压", value=135, timestamp="2026-08-17T08:00:00+00:00"),
        ]
    )
    merged = merge(existing, record)
    assert len(merged) == 2


def test_items_to_nodes_skips_invalid_keeps_crisis():
    validated = validate(_bp_record(185, 95)) + validate(_bp_record(300, 95))
    nodes, crisis = items_to_nodes(validated)
    assert len(nodes) == 1  # 300 被拦截
    assert len(crisis) == 1
    assert crisis[0].crisis_pending is True


def test_items_to_nodes_vitalsign_has_date_and_merge_key():
    validated = validate(_bp_record(155, 95))
    nodes, _ = items_to_nodes(validated)
    expected_date = validated[0].item.timestamp[:10]
    assert nodes[0].props["date"] == expected_date
    assert nodes[0].merge_key == f"VitalSign|血压|{expected_date}"


async def test_write_creates_patient_and_relation():
    driver = FakeDriver()
    nodes, _ = items_to_nodes(validate(_bp_record(155, 95)))
    await write(nodes, "张大爷", driver)
    cyphers = [c for c, _ in driver.calls]
    assert any("MATCH (p:Patient {name: $patient_id})" in c for c in cyphers)
    assert any("MERGE (n:Patient:`VitalSign`" in c for c in cyphers)
    assert any("`测量_AT`" in c for c in cyphers)


async def test_write_empty_no_query():
    driver = FakeDriver()
    await write([], "张大爷", driver)
    assert driver.calls == []


async def test_write_never_medication_label():
    driver = FakeDriver()
    record = PatientRecord(
        records=[
            RecordItem(type="vitalsign", name="血压", value=140, timestamp="2026-08-16T08:00:00+00:00"),
            RecordItem(type="emotion", name="焦虑", timestamp="2026-08-16T08:00:00+00:00"),
        ]
    )
    await write(merge([], record), "张大爷", driver)
    all_cyphers = " ".join(c for c, _ in driver.calls)
    assert "Medication" not in all_cyphers


async def test_memory_pipeline_full_flow():
    driver = FakeDriver()
    llm = FakeLLM(
        {
            "records": [
                {"type": "vitalsign", "name": "血压", "value": 155, "diastolic": 95, "unit": "mmHg", "timestamp": "2026-08-16T08:00:00+00:00"}
            ]
        }
    )
    nodes, crisis = await memory_pipeline("今天早上量血压 155/95", "张大爷", llm, driver)
    assert len(nodes) == 1
    assert crisis == []
    assert any("VitalSign" in c for c, _ in driver.calls)


async def test_memory_pipeline_crisis_flagged():
    driver = FakeDriver()
    llm = FakeLLM(
        {
            "records": [
                {"type": "vitalsign", "name": "血压", "value": 185, "diastolic": 100, "unit": "mmHg", "timestamp": "2026-08-16T08:00:00+00:00"}
            ]
        }
    )
    nodes, crisis = await memory_pipeline("收缩压 185", "张大爷", llm, driver)
    assert len(crisis) == 1
    assert crisis[0].crisis_pending is True


async def test_patient_summary_query_shape():
    driver = FakeDriver()
    summary = await patient_summary("张大爷", driver)
    assert summary["patient_id"] == "张大爷"
    assert "vital_signs" in summary and "emotions" in summary and "life_events" in summary
    cyphers = " ".join(c for c, _ in driver.calls)
    assert "测量_AT" in cyphers and "经历" in cyphers