"""
康龙化成(300759) 持仓分析 - 使用 OpenAshare 数据源 + AI 分析
"""
import sys, json, pandas as pd
from datetime import datetime
sys.path.insert(0, r'D:\IdeaProjects\astock\repos\OpenAshare')
from Ashare import get_price
from openai import OpenAI

# === 配置 ===
API_KEY = "sk-cjbpzqvq009y2kUH3Bc6ZRYdGufPJnH2b4cDkzLIzAeQ4CKi"
API_BASE = "https://aihub.firstshare.cn/v1"
MODEL = "deepseek-v4-pro-max"
client = OpenAI(api_key=API_KEY, base_url=API_BASE)
# ==========

code, name = "300759", "康龙化成"
print(f"\n🔍 获取 {name}({code}) 数据...")

df = get_price("sz" + code, frequency="1d", count=35)
df = df.reset_index()
df.columns = ["trade_date","open","high","low","close","volume"]
df["change_pct"] = df["close"].pct_change() * 100
df["ma5"] = df["close"].rolling(5).mean().round(2)
df["ma10"] = df["close"].rolling(10).mean().round(2)
df["ma20"] = df["close"].rolling(20).mean().round(2)

last = df.iloc[-1]
last30 = df.tail(30)

snap = {
    "name": name, "code": code,
    "price": float(last["close"]),
    "chg": round(float(last["change_pct"]), 2),
    "open": float(last["open"]),
    "high": float(last["high"]),
    "low": float(last["low"]),
    "pre_close": float(df.iloc[-2]["close"]),
    "volume": float(last["volume"]),
    "high20d": float(df["high"].tail(20).max()),
    "low20d": float(df["low"].tail(20).min()),
    "ma5": float(last["ma5"]),
    "ma10": float(last["ma10"]),
    "ma20": float(last["ma20"]),
    "up30": int((last30["change_pct"] > 0).sum()),
    "down30": int((last30["change_pct"] < 0).sum()),
    "max_up": round(float(last30["change_pct"].max()), 2),
    "max_down": round(float(last30["change_pct"].min()), 2),
    "vol_5d_avg": round(float(df["volume"].tail(5).mean() / 1e4), 0),
    "vol_today": round(float(last["volume"] / 1e4), 0),
}

print(f"  ✅ {name} 今日收盘 {snap['price']} 元  涨跌 {snap['chg']}%\n")

# 打印最近10日明细
print("━━━ 最近10日明细 ━━━")
cols = ["trade_date","close","change_pct","volume","ma5","ma10","ma20"]
print(f"{'日期':>10}  {'收盘':>5}  {'涨幅':>7}  {'成交量(万)':>10}  {'MA5':>6}  {'MA10':>6}  {'MA20':>6}")
for i in range(-10, 0):
    row = df.iloc[i]
    dt = str(row["trade_date"])[:10]
    c = row["close"]
    ch = row["change_pct"]
    v = row["volume"] / 1e4
    m5 = row["ma5"]
    m10 = row["ma10"]
    m20 = row["ma20"]
    arrow = "🔴" if ch < 0 else "🟢" if ch > 0 else "⚪"
    print(f"{dt:>10}  {c:>5.2f}  {arrow}{ch:>+6.2f}%  {v:>10.0f}  {m5:>6.2f}  {m10:>6.2f}  {m20:>6.2f}")

print()
print(f"📊 关键指标")
print(f"  20日最高: {snap['high20d']}    20日最低: {snap['low20d']}")
print(f"  30日上涨天数: {snap['up30']}   下跌天数: {snap['down30']}")
print(f"  30日最大单日涨幅: {snap['max_up']}%   最大单日跌幅: {snap['max_down']}%")
print(f"  今日量/5日均量比: {snap['vol_today']}/{snap['vol_5d_avg']}")
print(f"  成本价: 待补充   浮盈/亏: 待补充")

# AI分析
print("\n🧠 AI分析中...\n")
prompt = f"""你是一对一投资顾问。请分析用户持有的单只股票，给出次日操作建议。

数据截止：{datetime.now().strftime('%Y-%m-%d')}
大盘背景：今日A股沪指+1.16%，创业板+3.66%，科技(电子+5.43%)+有色(+4.13%)领涨，消费偏弱(食品饮料-1.19%)

股票数据：
{json.dumps(snap, ensure_ascii=False, indent=2)}

请按以下格式输出：
【股票名】
- 现状判断：一句话概括
- 关键位置：支撑位/压力位
- 明日走势预判：涨/跌/震荡 + 概率 + 理由
- 操作建议：持有/减仓/清仓/加仓 + 具体策略
- 止损位：
- 止盈位：
"""

resp = client.chat.completions.create(
    model=MODEL, messages=[{"role":"user","content":prompt}],
    temperature=0.2, max_tokens=2000
)
print(resp.choices[0].message.content)
