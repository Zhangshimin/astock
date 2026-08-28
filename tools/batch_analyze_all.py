"""
全A股批量趋势分析脚本
- 获取所有A股（排除ST/退市）的趋势分析
- 输出：个股MD + 汇总报告
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from statistics import mean

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "repos" / "OpenAshare"))
from Ashare import get_price  # type: ignore

from sentiment_analyzer import SentimentAnalyzer

# ===== 路径配置 =====
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALL_CODE_CSV = PROJECT_ROOT / "repos" / "adata" / "adata" / "stock" / "info" / "cache" / "all_code.csv"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "by-requirement" / "全A股趋势分析"
DETAIL_DIR = OUTPUT_DIR / "stocks"

# ===== 过滤配置 =====
FETCH_COUNT = 120          # 获取120个交易日数据
MIN_DATA_LEN = 60          # 最少需要60个交易日数据
STOCK_LIMIT = 0            # 0=全部，>0则限制数量（用于调试）


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
    exchange: str = ""
    # 情绪面字段
    sentiment_score: int = 0
    sentiment_details: list[str] = field(default_factory=list)
    hot_rank: int = 0
    billboard_info: str = ""
    mine_score: int = 0


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


def score_stock(
    close: float, ma5: float, ma10: float, ma20: float, ma60: float,
    pct: float, gain5: float, gain20: float, vol_ratio_5: float, bb_pos: float
) -> tuple[int, list[str], list[str]]:
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


def fetch_stock_report(code: str, name: str, exchange: str, sa: SentimentAnalyzer | None = None) -> StockReport | None:
    """获取单只股票的分析报告"""
    pref = market_prefix(code)
    market_code = f"{pref}{code}"
    try:
        raw = get_price(market_code, frequency="1d", count=FETCH_COUNT)
    except Exception as e:
        print(f"  ⚠️  {name}({code}) 数据获取异常: {e}")
        return None

    if raw is None or len(raw) < MIN_DATA_LEN:
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
    df["bb_pos"] = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]) * 100

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

    trend_label = classify_trend(
        safe_float(last["close"]), safe_float(last["ma20"]),
        safe_float(last["ma60"]), gain_20d, gain_60d
    )
    momentum_label = classify_momentum(
        safe_float(last["pct"]), gain_5d,
        safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0)
    )
    risk_label = classify_risk(
        safe_float(last["bb_pos"]),
        safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0),
        safe_float(last["close"]), high20, low20
    )

    recent_rows = []
    for _, row in df.tail(8).iterrows():
        recent_rows.append({
            "date": str(row["trade_date"])[:10],
            "close": f"{safe_float(row['close']):.2f}",
            "pct": f"{safe_float(row['pct']):+.2f}%",
            "volume": f"{int(safe_float(row['volume']) / 10000)}万",
            "ma5": f"{safe_float(row['ma5']):.2f}",
            "ma10": f"{safe_float(row['ma10']):.2f}",
        })

    # 情绪面数据
    sentiment_score = 0
    sentiment_details = []
    hot_rank = 0
    billboard_info = ""
    mine_score = 0
    if sa is not None:
        sinfo = sa.get_stock_sentiment(code)
        sentiment_score = sinfo["sentiment_score"]
        sentiment_details = sinfo["details"]
        hot_rank = sinfo.get("hot_rank", 0)
        if "billboard" in sinfo:
            bb = sinfo["billboard"]
            direction = "涨停" if bb["is_up"] else "跌停"
            billboard_info = f"{direction} {bb['reason']}"
        # 扫雷（仅对高分热股做，节约API调用）
        if hot_rank > 0 and hot_rank <= 30:
            mine = sa.get_mine_clearance(code)
            if mine:
                mine_score = mine["score"]

    return StockReport(
        code=code, name=name, market_code=market_code, exchange=exchange,
        last_date=str(last["trade_date"])[:10],
        close=round(safe_float(last["close"]), 2),
        pct=round(safe_float(last["pct"]), 2),
        ma5=round(safe_float(last["ma5"]), 2),
        ma10=round(safe_float(last["ma10"]), 2),
        ma20=round(safe_float(last["ma20"]), 2),
        ma60=round(safe_float(last["ma60"]), 2),
        vol_ratio_5=round(safe_float(last["volume"] / last["vol_ma5"] if last["vol_ma5"] else 0), 2),
        vol_ratio_20=round(safe_float(last["volume"] / last["vol_ma20"] if last["vol_ma20"] else 0), 2),
        high20=round(high20, 2), low20=round(low20, 2),
        gain_5d=round(gain_5d, 2), gain_20d=round(gain_20d, 2), gain_60d=round(gain_60d, 2),
        up_days_20=up_days_20, down_days_20=down_days_20,
        bb_pos=round(safe_float(last["bb_pos"]), 1),
        trend_label=trend_label, momentum_label=momentum_label, risk_label=risk_label,
        score=score, reasons=reasons, risks=risks, recent_rows=recent_rows,
        sentiment_score=sentiment_score, sentiment_details=sentiment_details,
        hot_rank=hot_rank, billboard_info=billboard_info, mine_score=mine_score,
    )


def render_stock_md(r: StockReport) -> str:
    """渲染个股趋势分析MD"""
    recent_table = "\n".join(
        f"| {row['date']} | {row['close']} | {row['pct']} | {row['volume']} | {row['ma5']} | {row['ma10']} |"
        for row in r.recent_rows
    )
    reasons = "\n".join(f"- {item}" for item in r.reasons) or "- 暂无明显正向结构。"
    risks = "\n".join(f"- {item}" for item in r.risks) or "- 暂无突出的风险提示。"

    # 情绪面
    sentiment_block = ""
    if r.sentiment_score > 0 or r.hot_rank > 0 or r.billboard_info:
        sentiment_block = "\n## 情绪面\n\n"
        if r.sentiment_details:
            for d in r.sentiment_details:
                sentiment_block += f"- {d}\n"
        if r.billboard_info:
            sentiment_block += f"- 龙虎榜：{r.billboard_info}\n"
        if r.mine_score > 0:
            sentiment_block += f"- 扫雷评分：{r.mine_score}/100（越高越安全）\n"

    return (
        f"# {r.name}（{r.code}）趋势分析\n\n"
        f"- 分析日期：{r.last_date}\n"
        f"- 市场代码：`{r.market_code}`\n"
        f"- 交易所：`{r.exchange}`\n"
        f"- 趋势标签：**{r.trend_label}**\n"
        f"- 动量标签：**{r.momentum_label}**\n"
        f"- 风险标签：**{r.risk_label}**\n"
        f"- 综合评分：**{r.score}/100**"
        + (f"（情绪加分：+{r.sentiment_score}）" if r.sentiment_score > 0 else "")
        + "\n\n"
        f"## 核心快照\n\n"
        f"- 收盘价：`{r.close}`，当日涨跌：`{r.pct:+.2f}%`\n"
        f"- 均线：`MA5 {r.ma5}` / `MA10 {r.ma10}` / `MA20 {r.ma20}` / `MA60 {r.ma60}`\n"
        f"- 量比：`5日 {r.vol_ratio_5}` / `20日 {r.vol_ratio_20}`\n"
        f"- 20日区间：`{r.low20} ~ {r.high20}`\n"
        f"- 区间收益：`5日 {r.gain_5d:+.2f}%` / `20日 {r.gain_20d:+.2f}%` / `60日 {r.gain_60d:+.2f}%`\n"
        f"- 20日涨跌天数：`{r.up_days_20}涨 / {r.down_days_20}跌`\n"
        f"- 布林位置：`{r.bb_pos}%`\n\n"
        f"## 看多依据\n\n{reasons}\n\n"
        f"## 风险提示\n\n{risks}\n\n"
        f"## 交易观察点\n\n"
        f"- 若后续放量站稳 `20日高点 {r.high20}`，趋势更容易从震荡转向强化。\n"
        f"- 若回落并失守 `MA20 {r.ma20}`，短线大概率先回到区间消化。\n"
        f"- 量能若持续低于 `5日均量`，更适合按震荡而非主升来理解。\n\n"
        + sentiment_block
        + f"## 最近 8 个交易日\n\n"
        f"| 日期 | 收盘 | 涨跌幅 | 成交量 | MA5 | MA10 |\n"
        f"|---|---:|---:|---:|---:|---:|\n"
        f"{recent_table}\n"
    )


def render_summary_md(reports: list[StockReport], failed_count: int, total_count: int,
                      sentiment_data: dict | None = None) -> str:
    """渲染全A股汇总报告"""
    sorted_reports = sorted(reports, key=lambda x: x.score, reverse=True)
    top30 = sorted_reports[:30]
    weak30 = sorted_reports[-30:]

    # 按交易所分组
    sh_reports = [r for r in sorted_reports if r.exchange == "SH"]
    sz_reports = [r for r in sorted_reports if r.exchange == "SZ"]
    bj_reports = [r for r in sorted_reports if r.exchange == "BJ"]

    # 趋势分布
    trend_counts: dict[str, int] = {}
    for r in sorted_reports:
        trend_counts[r.trend_label] = trend_counts.get(r.trend_label, 0) + 1

    # 评分区间分布
    score_dist = {">=80": 0, "70-79": 0, "60-69": 0, "50-59": 0, "40-49": 0, "30-39": 0, "<30": 0}
    for r in sorted_reports:
        if r.score >= 80:
            score_dist[">=80"] += 1
        elif r.score >= 70:
            score_dist["70-79"] += 1
        elif r.score >= 60:
            score_dist["60-69"] += 1
        elif r.score >= 50:
            score_dist["50-59"] += 1
        elif r.score >= 40:
            score_dist["40-49"] += 1
        elif r.score >= 30:
            score_dist["30-39"] += 1
        else:
            score_dist["<30"] += 1

    avg_score = round(mean(r.score for r in reports), 1) if reports else 0
    rising_count = len([r for r in reports if r.pct > 0])

    def fmt_bullets(items: list[StockReport]) -> str:
        return "\n".join(
            f"- {item.name}（{item.code}）：{item.score}分 · {item.close}元 · "
            f"{item.pct:+.2f}% · {item.trend_label} · {item.momentum_label} · {item.risk_label}"
            for item in items
        )

    trend_lines = "\n".join(
        f"- {label}：{count}只（{count/len(sorted_reports)*100:.1f}%）"
        for label, count in sorted(trend_counts.items(), key=lambda x: x[1], reverse=True)
    )
    score_lines = "\n".join(
        f"- {range_name}：{count}只" for range_name, count in score_dist.items()
    )

    # 行业初步分组（按名称关键词）
    sector_map: dict[str, int] = {}
    sector_keywords = {
        "银行": "银行", "证券": "券商", "保险": "保险",
        "医药": "医药", "医疗": "医药", "生物": "医药",
        "科技": "科技", "信息": "科技", "软件": "科技", "电子": "科技", "通信": "科技",
        "电力": "电力能源", "能源": "电力能源", "煤炭": "电力能源", "石油": "电力能源", "燃气": "电力能源",
        "房地产": "地产", "置业": "地产",
        "汽车": "汽车", "整车": "汽车",
        "食品": "消费", "饮料": "消费", "乳业": "消费",
        "国防": "军工", "军工": "军工", "航天": "军工", "船舶": "军工",
        "化工": "化工周期", "钢铁": "化工周期", "有色": "化工周期", "材料": "化工周期",
        "建筑": "基建地产", "建设": "基建地产", "建材": "基建地产",
        "交通": "交通运输", "运输": "交通运输", "港口": "交通运输", "航运": "交通运输", "航空": "交通运输",
        "机械": "制造", "装备": "制造", "重工": "制造",
    }
    for r in sorted_reports:
        matched = False
        for kw, sector in sector_keywords.items():
            if kw in r.name:
                sector_map[sector] = sector_map.get(sector, 0) + 1
                matched = True
                break
        if not matched:
            sector_map["其他"] = sector_map.get("其他", 0) + 1
    sector_lines = "\n".join(
        f"- {sector}：{count}只"
        for sector, count in sorted(sector_map.items(), key=lambda x: x[1], reverse=True)
    )

    # Sentiment section
    sentiment_md = ""
    if sentiment_data:
        hi = sentiment_data.get("hot_industry", {})
        hc = sentiment_data.get("hot_concept", {})
        hs = sentiment_data.get("hot_stocks", {})
        bb = sentiment_data.get("billboard", {})
        nf = sentiment_data.get("north_flow", {})

        sentiment_md = "\n## 今日情绪面\n\n"
        market_sent = SentimentAnalyzer().calc_market_sentiment_score(hi, hs)
        sentiment_md += f"- 大盘情绪评分：**{market_sent['score']}**（{market_sent['level']}）\n"
        for r_line in market_sent["reasons"]:
            sentiment_md += f"- {r_line}\n"
        sentiment_md += "\n"

        if hi.get("status") == "ok":
            sentiment_md += "### 热门行业 TOP5\n\n"
            for i in hi["items"][:5]:
                arrow = "+" if i["change_pct"] > 0 else ""
                sentiment_md += f"- {i['rank']}. {i['name']}（{arrow}{i['change_pct']:+.2f}%）\n"
            sentiment_md += "\n"

        if hc.get("status") == "ok":
            sentiment_md += "### 热门概念 TOP5\n\n"
            for i in hc["items"][:5]:
                arrow = "+" if i["change_pct"] > 0 else ""
                sentiment_md += f"- {i['rank']}. {i['name']}（{arrow}{i['change_pct']:+.2f}%）\n"
            sentiment_md += "\n"

        if nf.get("status") == "ok":
            net = nf.get("net_tgt", 0)
            sentiment_md += f"### 北向资金\n\n合计净买入 **{net/1e8:+.2f}亿**\n\n"

        if bb.get("status") == "ok":
            up_bb = [i for i in bb["items"] if i["is_up"]]
            sentiment_md += f"### 龙虎榜\n\n- 上榜：{len(bb['items'])}只（涨{len(up_bb)}只/跌{len(bb['items'])-len(up_bb)}只）\n\n"

    return (
        f"# 全A股趋势分析总报告\n\n"
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- 数据源：OpenAshare\n"
        f"- 分析周期：近{FETCH_COUNT}个交易日\n\n"
        f"## 统计概览\n\n"
        f"- 目标股票：`{total_count}` 只\n"
        f"- 成功分析：`{len(reports)}` 只\n"
        f"- 获取失败：`{failed_count}` 只\n"
        f"- 平均评分：`{avg_score}`\n"
        f"- 当日上涨家数：`{rising_count}` 只（{rising_count/len(reports)*100:.1f}%）\n\n"
        f"### 按交易所\n\n"
        f"- 沪市（SH）：`{len(sh_reports)}` 只，平均评分 `{round(mean(r.score for r in sh_reports), 1) if sh_reports else 0}`\n"
        f"- 深市（SZ）：`{len(sz_reports)}` 只，平均评分 `{round(mean(r.score for r in sz_reports), 1) if sz_reports else 0}`\n"
        f"- 北交所（BJ）：`{len(bj_reports)}` 只，平均评分 `{round(mean(r.score for r in bj_reports), 1) if bj_reports else 0}`\n\n"
        f"## 趋势结构分布\n\n{trend_lines}\n\n"
        f"## 评分分布\n\n{score_lines}\n\n"
        f"## 行业分布（简略）\n\n{sector_lines}\n\n"
        f"## 最强 30 只（高评分候选）\n\n{fmt_bullets(top30)}\n\n"
        f"## 最弱 30 只（需警惕/回避）\n\n{fmt_bullets(weak30)}\n\n"
        f"## 选股建议\n\n"
        f"- **主升趋势 + 高分（>=80）** 的股票是关注重点，尤其放量上涨的。\n"
        f"- **区间震荡** 占比过高说明市场整体风格偏防御。\n"
        f"- **走弱下行** 的股票若无基本面变化，建议回避。\n"
        f"- 具体选股还需结合行业景气度、资金流向、基本面数据综合判断。\n\n"
        f"## 免责声明\n\n"
        f"> 本报告基于技术指标自动生成，仅供学习参考，不构成投资建议。\n"
        f"> 数据来源：OpenAshare，可能存在延迟或误差。\n\n"
        f"## 明细目录\n\n"
        f"- 个股报告目录：`docs/by-requirement/全A股趋势分析/stocks/`\n"
    )


def safe_filename(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if c in invalid else c for c in name)
    return cleaned.strip().rstrip(".")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_DIR.mkdir(parents=True, exist_ok=True)

    print("📂 加载全A股列表...")
    df = pd.read_csv(ALL_CODE_CSV, dtype={"stock_code": str})
    df = df[df["list_date"].notna() & (df["list_date"] != "")].copy()
    df = df[~df["short_name"].str.contains("ST|退|PT", na=False)]
    df = df[df["stock_code"].notna()]
    df["stock_code"] = df["stock_code"].str.zfill(6)
    # 去重
    df = df.drop_duplicates(subset=["stock_code"])

    # 排序：深交所 > 上交所 > 北交所
    exchange_order = {"SZ": 0, "SH": 1, "BJ": 2}
    df["exchange_order"] = df["exchange"].map(exchange_order).fillna(9)
    df = df.sort_values("exchange_order")
    df = df.reset_index(drop=True)

    total = len(df)
    if STOCK_LIMIT > 0:
        df = df.head(STOCK_LIMIT)
        print(f"  🔧 限制模式：只分析前 {STOCK_LIMIT} 只")

    print(f"\n📊 待分析股票：{len(df)} 只")
    print(f"  - 上交所 SH：{len(df[df['exchange']=='SH'])}")
    print(f"  - 深交所 SZ：{len(df[df['exchange']=='SZ'])}")
    print(f"  - 北交所 BJ：{len(df[df['exchange']=='BJ'])}")

    # 初始化情绪面分析器（预加载热股100/龙虎榜/热门板块）
    print("\n🧠 加载情绪面数据...")
    sa = SentimentAnalyzer()
    sentiment_data = sa.fetch_all()
    for sk, sv in sentiment_data.items():
        if sk == "timestamp":
            continue
        st = sv.get("status", "?")
        si = len(sv.get("items", [])) if isinstance(sv.get("items"), list) else "N/A"
        print(f"  {sk}: {st} ({si})")
    print()

    reports: list[StockReport] = []
    failed_count = 0
    t_start = time.time()

    for idx, row in df.iterrows():
        code = row["stock_code"]
        name = row["short_name"]
        exchange = row["exchange"]

        report = fetch_stock_report(code, name, exchange, sa=sa)
        if report is None:
            failed_count += 1
            print(f"  [{(idx+1)/total*100:>5.1f}%] ❌ {code} {name} - 数据不足或获取失败")
            continue

        reports.append(report)

        # 写个股MD
        stock_md = render_stock_md(report)
        stock_file = DETAIL_DIR / f"{code}-{safe_filename(name)}.md"
        stock_file.write_text(stock_md, encoding="utf-8")

        elapsed = time.time() - t_start
        eta = (elapsed / (idx + 1)) * (total - idx - 1)
        sentiment_tag = f" 情绪+{report.sentiment_score}" if report.sentiment_score > 0 else ""
        print(
            f"  [{(idx+1)/total*100:>5.1f}%] ✅ {name}({code}) "
            f"{report.close}元 {report.pct:+.2f}% 评分{report.score}{sentiment_tag}"
            f" | 已用{elapsed:.0f}s 预计剩余{eta:.0f}s"
        )

    t_total = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"📈 分析完成！")
    print(f"  - 成功：{len(reports)} 只")
    print(f"  - 失败：{failed_count} 只")
    print(f"  - 用时：{t_total:.0f}秒（{t_total/60:.1f}分钟）")
    print(f"  - 个股报告：{DETAIL_DIR}")

    # 汇总报告
    summary_md = render_summary_md(reports, failed_count, total, sentiment_data=sentiment_data)
    (OUTPUT_DIR / "README.md").write_text(summary_md, encoding="utf-8")
    print(f"  - 汇总报告：{OUTPUT_DIR / 'README.md'}")

    # 导出JSON数据（便于后续分析）
    json_data = []
    for r in reports:
        d = asdict(r)
        d.pop("recent_rows", None)
        d.pop("reasons", None)
        d.pop("risks", None)
        json_data.append(d)
    (OUTPUT_DIR / "all_results.json").write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  - JSON数据：{OUTPUT_DIR / 'all_results.json'}")

    # 输出TOP10简报
    print(f"\n{'='*60}")
    print("🏆 TOP 10 强势股")
    print(f"{'='*60}")
    sorted_reports = sorted(reports, key=lambda x: x.score, reverse=True)
    for i, r in enumerate(sorted_reports[:10]):
        arrow = "🟢" if r.pct > 0 else "🔴"
        print(f"  {i+1}. {r.name}（{r.code}）{r.close}元 {arrow}{r.pct:+.2f}% 评分{r.score} {r.trend_label}")
    print(f"\n📉 BOTTOM 5")
    for i, r in enumerate(sorted_reports[-5:]):
        arrow = "🟢" if r.pct > 0 else "🔴"
        print(f"  {len(sorted_reports)-4+i}. {r.name}（{r.code}）{r.close}元 {arrow}{r.pct:+.2f}% 评分{r.score} {r.trend_label}")


if __name__ == "__main__":
    main()
