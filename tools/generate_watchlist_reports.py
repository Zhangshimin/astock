from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
import sys

import pandas as pd

sys.path.insert(0, r"D:\IdeaProjects\astock\repos\OpenAshare")

from Ashare import get_price  # type: ignore


OUTPUT_DIR = Path(r"D:\IdeaProjects\astock\docs\by-requirement\26.06趋势票\同花顺自选趋势票")
DETAIL_DIR = OUTPUT_DIR / "stocks"

ALL_CODE_CSV = Path(r"D:\IdeaProjects\astock\repos\adata\adata\stock\info\cache\all_code.csv")

RAW_WATCHLIST = [
    ("天健集团", "000090"),
    ("福星股份", "000926"),
    ("步步高", "002251"),
    ("春秋电子", "002639"),
    ("特力A", "000025"),
    ("深南电A", "000037"),
    ("中成股份", "000151"),
    ("冰山冷热", "000530"),
    ("粤电力A", "000539"),
    ("华锋股份", "002806"),
    ("和胜股份", "002824"),
    ("风华高科", "000636"),
    ("阳光股份", "000608"),
    ("酒鬼酒", "000799"),
    ("甘肃能源", "000791"),
    ("惠天热电", "000692"),
    ("新能泰山", "000720"),
    ("晋控电力", "000767"),
    ("国风新材", "000859"),
    ("华能国际", "600011"),
    ("中央商场", "600280"),
    ("云南锗业", "002428"),
    ("青龙管业", "002457"),
    ("江海股份", "002484"),
    ("光大嘉宝", "600622"),
    ("金安国纪", "002636"),
    ("华电能源", "600726"),
    ("江苏国信", "002608"),
    ("华联股份", "000882"),
    ("宁波中百", "600857"),
    ("百大集团", "600865"),
    ("广安爱众", "600979"),
    ("华银电力", "600744"),
    ("三江购物", "601116"),
    ("陕鼓动力", "601369"),
    ("北辰实业", "601588"),
    ("科林电气", "603050"),
    ("白云电器", "603861"),
    ("国泰集团", "603977"),
    ("皖维高新", "600063"),
    ("诺德股份", "600110"),
    ("香江控股", "600162"),
    ("中国巨石", "600176"),
    ("铜峰电子", "600237"),
    ("华微电子", "600360"),
    ("交大昂立", "600530"),
    ("宁波能源", "600982"),
    ("远东股份", "600869"),
    ("合锻智能", "603011"),
    ("国检集团", "603060"),
    ("得邦照明", "603303"),
    ("火炬电子", "603678"),
    ("春秋电子2", "603890"),
    ("金时科技", "002951"),
    ("鸿远电子", "603267"),
    ("科瑞技术", "002957"),
    ("博杰股份", "002975"),
    ("粤高速A", "000429"),
    ("皖能电力", "000543"),
    ("北部湾港", "000582"),
    ("广州发展", "600098"),
    ("太龙药业", "600222"),
    ("金房能源", "001210"),
    ("利仁科技", "001259"),
    ("林洋能源", "601222"),
    ("金海高科", "603311"),
    ("宝新能源", "000690"),
    ("湖北能源", "000883"),
    ("长源电力", "000966"),
    ("广西能源", "600310"),
    ("京能电力", "600578"),
    ("申能股份", "600642"),
    ("大连热电", "600719"),
    ("恒源煤电", "600971"),
    ("淮北矿业", "600985"),
    ("节能风电", "601016"),
    ("大唐发电", "601991"),
    ("迪生力", "603335"),
    ("迎丰股份", "605055"),
    ("银星能源", "000862"),
    ("永泰能源", "600157"),
]


@dataclass
class StockReport:
    code: str
    name: str
    market_code: str
    last_date: str
    close: float
    pct: float
    ma5: float
    ma10: float
    ma20: float
    ma60: float
    vol_ratio_5: float
    vol_ratio_20: float
    high20: float
    low20: float
    gain_5d: float
    gain_20d: float
    gain_60d: float
    up_days_20: int
    down_days_20: int
    bb_pos: float
    trend_label: str
    momentum_label: str
    risk_label: str
    score: int
    reasons: list[str]
    risks: list[str]
    recent_rows: list[dict[str, str]]


def load_name_map() -> dict[str, str]:
    df = pd.read_csv(ALL_CODE_CSV, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].str.zfill(6)
    return dict(zip(df["stock_code"], df["short_name"]))


def market_prefix(code: str) -> str:
    if code.startswith(("60", "68", "90")):
        return "sh"
    if code.startswith(("00", "30", "20")):
        return "sz"
    if code.startswith(("43", "83", "87", "92")):
        return "bj"
    return "sh"


def safe_float(value: float | int | None) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    return float(value)


def classify_trend(close: float, ma20: float, ma60: float, gain20: float, gain60: float) -> str:
    if close > ma20 > ma60 and gain20 > 8 and gain60 > 15:
        return "主升趋势"
    if close > ma20 and close > ma60 and gain20 > 0:
        return "震荡偏多"
    if close < ma20 and close < ma60 and gain20 < -5:
        return "走弱下行"
    return "区间震荡"


def classify_momentum(pct: float, gain5: float, vol_ratio_5: float) -> str:
    if pct > 4 and vol_ratio_5 > 1.2:
        return "强势放量"
    if gain5 > 6 and vol_ratio_5 < 0.9:
        return "缩量走高"
    if pct < -3 and vol_ratio_5 > 1.2:
        return "放量回撤"
    return "中性"


def classify_risk(bb_pos: float, vol_ratio_5: float, close: float, high20: float, low20: float) -> str:
    if bb_pos > 85 and vol_ratio_5 < 1.0:
        return "冲高钝化"
    if close >= high20 * 0.98:
        return "临近前高"
    if close <= low20 * 1.03:
        return "临近支撑"
    return "常规波动"


def score_stock(close: float, ma5: float, ma10: float, ma20: float, ma60: float, pct: float, gain5: float, gain20: float, vol_ratio_5: float, bb_pos: float) -> tuple[int, list[str], list[str]]:
    score = 50
    reasons: list[str] = []
    risks: list[str] = []

    if close > ma20:
        score += 8
        reasons.append("收盘站上MA20，中短线趋势仍偏多")
    else:
        score -= 8
        risks.append("收盘跌回MA20下方，趋势完整性不足")

    if close > ma60:
        score += 10
        reasons.append("收盘站上MA60，中期结构没有被破坏")
    else:
        score -= 10
        risks.append("收盘低于MA60，中期趋势承压")

    if ma5 > ma10 > ma20:
        score += 12
        reasons.append("短均线呈多头发散，短线资金合力较强")
    elif ma5 < ma10 < ma20:
        score -= 12
        risks.append("短均线空头排列，反弹更像修复而非主升")

    if vol_ratio_5 > 1.5 and pct > 0:
        score += 8
        reasons.append("放量上涨，量价配合较好")
    elif vol_ratio_5 < 0.8 and pct > 0:
        score -= 6
        risks.append("价涨量缩，追价意愿不足")
    elif vol_ratio_5 > 1.3 and pct < 0:
        score -= 8
        risks.append("放量下跌，抛压偏大")

    if gain5 > 8:
        score += 5
        reasons.append("近5日涨幅较强，短线趋势占优")
    elif gain5 < -8:
        score -= 5
        risks.append("近5日跌幅偏大，短线修复压力仍在")

    if gain20 > 15:
        score += 5
        reasons.append("近20日趋势明显向上")
    elif gain20 < -12:
        score -= 5
        risks.append("近20日趋势仍在下弯")

    if bb_pos > 85:
        score -= 4
        risks.append("布林位置接近上轨，短线容易先震荡再选择方向")
    elif bb_pos < 20:
        score += 4
        reasons.append("布林位置偏低，若量能改善更容易出现修复")

    return max(0, min(100, score)), reasons, risks


def fetch_stock_report(code: str, requested_name: str, official_name: str) -> StockReport | None:
    pref = market_prefix(code)
    market_code = f"{pref}{code}"
    raw = get_price(market_code, frequency="1d", count=120)
    if raw is None or len(raw) < 70:
        return None

    df = raw.reset_index()
    df.columns = ["trade_date", "open", "high", "low", "close", "volume"]
    df["trade_date"] = df["trade_date"].astype(str)
    df["pct"] = df["close"].pct_change() * 100
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std
    df["bb_pos"] = ((df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]) * 100)
    last = df.iloc[-1]
    high20 = safe_float(df["high"].tail(20).max())
    low20 = safe_float(df["low"].tail(20).min())
    gain_5d = (last["close"] / df.iloc[-6]["close"] - 1) * 100 if len(df) >= 6 else 0
    gain_20d = (last["close"] / df.iloc[-21]["close"] - 1) * 100 if len(df) >= 21 else 0
    gain_60d = (last["close"] / df.iloc[-61]["close"] - 1) * 100 if len(df) >= 61 else 0
    up_days_20 = int((df["pct"].tail(20) > 0).sum())
    down_days_20 = int((df["pct"].tail(20) < 0).sum())

    score, reasons, risks = score_stock(
        close=safe_float(last["close"]),
        ma5=safe_float(last["ma5"]),
        ma10=safe_float(last["ma10"]),
        ma20=safe_float(last["ma20"]),
        ma60=safe_float(last["ma60"]),
        pct=safe_float(last["pct"]),
        gain5=gain_5d,
        gain20=gain_20d,
        vol_ratio_5=safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0),
        bb_pos=safe_float(last["bb_pos"]),
    )

    trend_label = classify_trend(safe_float(last["close"]), safe_float(last["ma20"]), safe_float(last["ma60"]), gain_20d, gain_60d)
    momentum_label = classify_momentum(safe_float(last["pct"]), gain_5d, safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0))
    risk_label = classify_risk(safe_float(last["bb_pos"]), safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0), safe_float(last["close"]), high20, low20)

    recent_rows = []
    for _, row in df.tail(8).iterrows():
        recent_rows.append(
            {
                "date": str(row["trade_date"])[:10],
                "close": f"{safe_float(row['close']):.2f}",
                "pct": f"{safe_float(row['pct']):+.2f}%",
                "volume": f"{int(safe_float(row['volume']) / 10000)}万",
                "ma5": f"{safe_float(row['ma5']):.2f}",
                "ma10": f"{safe_float(row['ma10']):.2f}",
            }
        )

    report_name = official_name if official_name else requested_name
    if requested_name and requested_name != official_name and requested_name != f"{official_name}2":
        risks.append(f"原始名单名称识别为“{requested_name}”，已按股票代码校正为“{official_name}”")

    return StockReport(
        code=code,
        name=report_name,
        market_code=market_code,
        last_date=str(last["trade_date"])[:10],
        close=round(safe_float(last["close"]), 2),
        pct=round(safe_float(last["pct"]), 2),
        ma5=round(safe_float(last["ma5"]), 2),
        ma10=round(safe_float(last["ma10"]), 2),
        ma20=round(safe_float(last["ma20"]), 2),
        ma60=round(safe_float(last["ma60"]), 2),
        vol_ratio_5=round(safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0), 2),
        vol_ratio_20=round(safe_float(last["volume"] / last["vol_ma20"] if last["vol_ma20"] else 0), 2),
        high20=round(high20, 2),
        low20=round(low20, 2),
        gain_5d=round(gain_5d, 2),
        gain_20d=round(gain_20d, 2),
        gain_60d=round(gain_60d, 2),
        up_days_20=up_days_20,
        down_days_20=down_days_20,
        bb_pos=round(safe_float(last["bb_pos"]), 1),
        trend_label=trend_label,
        momentum_label=momentum_label,
        risk_label=risk_label,
        score=score,
        reasons=reasons,
        risks=risks,
        recent_rows=recent_rows,
    )


def render_stock_md(report: StockReport) -> str:
    recent_table = "\n".join(
        f"| {row['date']} | {row['close']} | {row['pct']} | {row['volume']} | {row['ma5']} | {row['ma10']} |"
        for row in report.recent_rows
    )
    reasons = "\n".join(f"- {item}" for item in report.reasons) or "- 暂无明显正向结构。"
    risks = "\n".join(f"- {item}" for item in report.risks) or "- 暂无突出的风险提示。"
    return f"# {report.name}（{report.code}）趋势分析\n\n" \
        f"- 分析日期：{report.last_date}\n" \
        f"- 市场代码：`{report.market_code}`\n" \
        f"- 趋势标签：**{report.trend_label}**\n" \
        f"- 动量标签：**{report.momentum_label}**\n" \
        f"- 风险标签：**{report.risk_label}**\n" \
        f"- 综合评分：**{report.score}/100**\n\n" \
        f"## 核心快照\n\n" \
        f"- 收盘价：`{report.close}`，当日涨跌：`{report.pct:+.2f}%`\n" \
        f"- 均线：`MA5 {report.ma5}` / `MA10 {report.ma10}` / `MA20 {report.ma20}` / `MA60 {report.ma60}`\n" \
        f"- 量比：`5日 {report.vol_ratio_5}` / `20日 {report.vol_ratio_20}`\n" \
        f"- 20日区间：`{report.low20} ~ {report.high20}`\n" \
        f"- 区间收益：`5日 {report.gain_5d:+.2f}%` / `20日 {report.gain_20d:+.2f}%` / `60日 {report.gain_60d:+.2f}%`\n" \
        f"- 20日涨跌天数：`{report.up_days_20}涨 / {report.down_days_20}跌`\n" \
        f"- 布林位置：`{report.bb_pos}%`\n\n" \
        f"## 看多依据\n\n{reasons}\n\n" \
        f"## 风险提示\n\n{risks}\n\n" \
        f"## 交易观察点\n\n" \
        f"- 若后续放量站稳 `20日高点 {report.high20}`，趋势更容易从震荡转向强化。\n" \
        f"- 若回落并失守 `MA20 {report.ma20}`，短线大概率先回到区间消化。\n" \
        f"- 量能若持续低于 `5日均量`，更适合按震荡而非主升来理解。\n\n" \
        f"## 最近 8 个交易日\n\n" \
        f"| 日期 | 收盘 | 涨跌幅 | 成交量 | MA5 | MA10 |\n" \
        f"|---|---:|---:|---:|---:|---:|\n" \
        f"{recent_table}\n"


def render_summary_md(reports: list[StockReport], unresolved: list[tuple[str, str]]) -> str:
    sorted_reports = sorted(reports, key=lambda item: item.score, reverse=True)
    top = sorted_reports[:15]
    weak = sorted_reports[-15:]
    trend_groups: dict[str, list[StockReport]] = {}
    for report in sorted_reports:
        trend_groups.setdefault(report.trend_label, []).append(report)

    def bullets(items: list[StockReport]) -> str:
        return "\n".join(
            f"- {item.name}（{item.code}）：{item.score}分，{item.trend_label}，{item.momentum_label}，{item.risk_label}"
            for item in items
        )

    group_lines = "\n".join(
        f"- {label}：{len(items)}只" for label, items in sorted(trend_groups.items(), key=lambda pair: len(pair[1]), reverse=True)
    )

    unresolved_lines = "\n".join(f"- 原始项：{name} / {code}" for name, code in unresolved) or "- 无"
    avg_score = round(mean(item.score for item in reports), 1) if reports else 0
    strong_count = len([item for item in reports if item.score >= 70])
    weak_count = len([item for item in reports if item.score < 45])
    rising_count = len([item for item in reports if item.pct > 0])
    return f"# 26.06趋势票批量分析总报告\n\n" \
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" \
        f"- 成功分析：`{len(reports)}` 只\n" \
        f"- 平均评分：`{avg_score}`\n" \
        f"- 强势候选（>=70分）：`{strong_count}` 只\n" \
        f"- 偏弱候选（<45分）：`{weak_count}` 只\n" \
        f"- 当日上涨家数：`{rising_count}` 只\n\n" \
        f"## 结构总览\n\n{group_lines}\n\n" \
        f"## 最强 15 只\n\n{bullets(top)}\n\n" \
        f"## 最弱 15 只\n\n{bullets(weak)}\n\n" \
        f"## 风格观察\n\n" \
        f"- 这批票整体以 **电力、能源、商贸零售、周期制造** 为主，明显带有防御和事件驱动并存的特征。\n" \
        f"- 若高分票集中在 `主升趋势` 与 `震荡偏多`，说明市场更偏好延续型趋势；若大量票停留在 `区间震荡`，则说明这份自选更像高波动观察池。\n" \
        f"- 低分票里如果普遍出现 `价涨量缩`、`跌回MA20`、`布林上轨钝化`，说明这波趋势交易已经进入分化阶段。\n\n" \
        f"## 未成功校正或未纳入项\n\n{unresolved_lines}\n\n" \
        f"## 明细目录\n\n- 个股报告目录：`docs/by-requirement/26.06趋势票/同花顺自选趋势票/stocks/`\n"


def safe_filename(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid else char for char in name)
    return cleaned.strip().rstrip(".")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)

    code_to_name = load_name_map()
    reports: list[StockReport] = []
    unresolved: list[tuple[str, str]] = []
    resolved_manifest: list[dict[str, str]] = []

    seen_codes: set[str] = set()
    for requested_name, code in RAW_WATCHLIST:
        normalized_code = str(code)[-6:].zfill(6)
        if normalized_code in seen_codes:
            continue
        seen_codes.add(normalized_code)
        official_name = code_to_name.get(normalized_code, "")
        if not official_name:
            unresolved.append((requested_name, normalized_code))
            continue
        report = fetch_stock_report(normalized_code, requested_name, official_name)
        if report is None:
            unresolved.append((requested_name, normalized_code))
            continue
        reports.append(report)
        resolved_manifest.append(
            {
                "requested_name": requested_name,
                "official_name": official_name,
                "code": normalized_code,
                "score": str(report.score),
                "trend": report.trend_label,
            }
        )
        stock_md = render_stock_md(report)
        stock_path = DETAIL_DIR / f"{normalized_code}-{safe_filename(official_name)}.md"
        stock_path.write_text(stock_md, encoding="utf-8")

    summary_md = render_summary_md(reports, unresolved)
    (OUTPUT_DIR / "README.md").write_text(summary_md, encoding="utf-8")
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(resolved_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "reports": len(reports),
        "unresolved": len(unresolved),
        "output": str(OUTPUT_DIR),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
