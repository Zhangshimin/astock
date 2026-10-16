"""Validated desktop settings shared with the uTools configuration form."""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

from astock_hub.intraday_backtest import IntradayStrategyConfig
from astock_hub.desktop_paths import resource_root

SCHEMA = json.loads((resource_root() / "utools" / "settings-schema.json").read_text(encoding="utf-8"))


def validate_settings(value: dict) -> dict:
    result = dict(value)
    for field in SCHEMA:
        key, kind = field["key"], field["type"]
        item = value.get(key, field["default"])
        valid = True
        if kind == "boolean":
            valid = isinstance(item, bool)
        elif kind in ("integer", "number"):
            valid = type(item) in (int, float) and math.isfinite(item) and field["min"] <= item <= field["max"]
            if valid and kind == "integer":
                valid = int(item) == item
        elif kind == "select":
            valid = isinstance(item, str) and item in field["options"]
        elif kind == "time":
            valid = isinstance(item, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item) is not None
        else:
            valid = isinstance(item, str)
        if not valid:
            raise ValueError(f"{field['label']}无效")
        result[key] = item
    if result["sellStart"] >= result["sellCutoff"]:
        raise ValueError("允许卖出开始时间必须早于结束时间")
    if result["displayStart"] == result["displayEnd"]:
        raise ValueError("显示开始与结束时间不能相同")
    if result["alertStart"] == result["alertEnd"]:
        raise ValueError("提醒开始与结束时间不能相同")
    if result["volumeShrink"] >= result["volumeSurge"]:
        raise ValueError("缩量倍数必须小于放量倍数")
    if result["sectorCode"] and not re.fullmatch(r"BK\d{4,6}", result["sectorCode"]):
        raise ValueError("板块代码格式应为 BK 加 4–6 位数字")
    poll = result.get("poll", 5)
    if type(poll) not in (int, float) or not math.isfinite(poll) or int(poll) != poll or not 3 <= poll <= 3600:
        raise ValueError("刷新间隔必须为 3–3600 秒整数")
    result["poll"] = int(poll)
    return result


def read_settings(path: Path | None) -> dict:
    return validate_settings(json.loads(path.read_text(encoding="utf-8")) if path else {})


def display_allowed(settings: dict, now: datetime) -> bool:
    if settings["weekdaysOnly"] and now.weekday() >= 5:
        return False
    if settings["displayMode"] == "always":
        return True
    start, end, current = settings["displayStart"], settings["displayEnd"], now.strftime("%H:%M")
    return start <= current < end if start < end else current >= start or current < end


def strategy_config(settings: dict) -> IntradayStrategyConfig:
    return IntradayStrategyConfig(
        sell_vwap_premium=settings["sellVwapPremium"] / 100,
        buyback_discount=settings["buybackDiscount"] / 100,
        chase_back_rise=settings["chaseBackRise"] / 100,
        cooldown_minutes=settings["cooldownMinutes"], max_cycles=settings["maxCycles"],
        sell_start=settings["sellStart"], sell_cutoff=settings["sellCutoff"],
    )


def write_status(config_path: Path | None, payload: dict) -> None:
    if config_path is None:
        return
    target = config_path.with_suffix('.status.json')
    temporary = target.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps({**payload, "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
