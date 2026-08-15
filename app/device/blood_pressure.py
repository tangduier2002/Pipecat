"""蓝牙设备接入 (预留接口, MRP 阶段默认关闭)。

硬件不进开发路径 (Spec #1 范围外)。本模块仅定义抽象接口,
MRP 阶段 BloodPressureDevice 读取返回 None, 数据来源为语音输入。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    systolic: int
    diastolic: int
    timestamp: str


class BloodPressureDevice:
    """血压计设备抽象接口。MRP 阶段默认无设备 (read → None)。"""

    async def read(self) -> Reading | None:
        """读取一次血压读数。无设备时返回 None。"""
        return None