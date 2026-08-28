"""
华菱线缆(001208) + 亚威股份(002559) 对比分析 + 评分
"""
import sys, json, math, pandas as pd
from datetime import datetime
sys.path.insert(0, r'D:\IdeaProjects\astock\repos\OpenAshare')
from Ashare import get_price
from openai import OpenAI

API_KEY = "sk-cjbpzqvq009y2kUH3Bc6ZRYdGufPJnH2b4cDkzLIzAeQ4CKi"
API_BASE = "https://aihub.firstshare.cn/v1"
MODEL = "deepseek-v4-flash"
client = OpenAI(api_key=API_KEY, base_url=API_BASE)

def norm(val, min_v, max_v, higher_better=True):
    """将值映射到0-10分"""
    if max_v == min_v:
        return 5
    ratio = (val - min_v) / (max_v - min_v)
    ratio = max(0, min(1, ratio))
    return round(ratio * 10 if higher_better else (1 - ratio) * 10, 1)

def calc_scores(snap):
    """基于技术面数据计算评分"""
    s = snap
    up_ratio = s["up30"] / max(s["up30"] + s["down30"], 1)  # 上涨占比
    pct_rank = (s["price"] - s["low20d"]) / max(s["high20d"] - s["low20d"], 0.01)  # 价格在20日区间位置 0~1
    
    ma_score = 0
    if s["price"] > s["ma5"]: ma_score += 3
    if s["price"] > s["ma10"]: ma_score += 3.5
    if s["price"] > s["ma20"]: ma_score += 3.5
    else:
        if s["ma5"] > s["ma10"]: ma_score += 2
        if s["ma10"] > s["ma20"]: ma_score += 2
    
    scores = {
        "趋势": round(ma_score, 1),
        "动量": round(norm(up_ratio, 0.2, 0.8) * 0.6 + norm(s["chg"], -0.5, 0.5) * 0.4, 1),
        "量价": round(
            norm(s["vol_ratio"], 0.5, 2.0) * 0.5 +
            (5 if s["chg"] > 0 and s["vol_ratio"] >= 0.8 else
             3 if s["chg"] > 0 else
             2.5 if s["vol_ratio"] >= 0.8 else 1),
        1),
        "位置": round(norm(pct_rank, 0, 1, False), 1),
        "回调深度": round(norm(abs(s["max_down"]), 2, 15), 1),
    }
    scores["总分"] = round(sum(scores.values()) / len(scores), 1)
    return scores

stocks = [("001208", "华菱线缆"), ("002559", "亚威股份")]
snapshots = []

for code, name in stocks:
    print(f"\n{'='*50}")
    print(f"🔍 获取 {name}({code}) 数据...")
    df = get_price("sz" + code, frequency="1d", count=35)
    if df is None or len(df) < 5:
        print(f"  ❌ {name} 数据不足")
        continue
    df = df.reset_index()
    df.columns = ["trade_date","open","high","low","close","volume"]
    df["change_pct"] = df["close"].pct_change() * 100
    df["ma5"] = df["close"].rolling(5).mean().round(2)
    df["ma10"] = df["close"].rolling(10).mean().round(2)
    df["ma20"] = df["close"].rolling(20).mean().round(2)

    last = df.iloc[-1]
    last30 = df.tail(30)
    ret_5d = (df.iloc[-1]["close"] / df.iloc[-6]["close"] - 1) * 100 if len(df) >= 6 else 0
    ret_10d = (df.iloc[-1]["close"] / df.iloc[-11]["close"] - 1) * 100 if len(df) >= 11 else 0

    snap = {
        "name": name, "code": code,
        "price": float(last["close"]),
        "chg": round(float(last["change_pct"]), 2),
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "pre_close": float(df.iloc[-2]["close"]),
        "volume": float(last["volume"]),
        "amount": round(float(last["volume"] * last["close"] / 1e8), 2),
        "high20d": float(df["high"].tail(20).max()),
        "low20d": float(df["low"].tail(20).min()),
        "ma5": float(last["ma5"]),
        "ma10": float(last["ma10"]),
        "ma20": float(last["ma20"]),
        "up30": int((last30["change_pct"] > 0).sum()),
        "down30": int((last30["change_pct"] < 0).sum()),
        "max_up": round(float(last30["change_pct"].max()), 2),
        "max_down": round(float(last30["change_pct"].min()), 2),
        "vol_ratio": round(float(last["volume"] / df["volume"].tail(5).mean()), 2),
        "ret_5d": round(float(ret_5d), 2),
        "ret_10d": round(float(ret_10d), 2),
    }
    snapshots.append(snap)

    print(f"  ✅ {name} 收盘 {snap['price']} 涨跌 {snap['chg']}%  5日涨跌 {snap['ret_5d']}%")

    print(f"\n━━━ {name}({code}) 最近10日明细 ━━━")
    print(f"{'日期':>10}  {'收盘':>5}  {'涨幅':>7}  {'成交量(万)':>10}  {'MA5':>6}  {'MA10':>6}  {'MA20':>6}")
    for i in range(-10, 0):
        row = df.iloc[i]
        arrow = "🔴" if row["change_pct"] < 0 else "🟢" if row["change_pct"] > 0 else "⚪"
        print(f"{str(row['trade_date'])[:10]:>10}  {row['close']:>5.2f}  {arrow}{row['change_pct']:>+6.2f}%  {row['volume']/1e4:>10.0f}  {row['ma5']:>6.2f}  {row['ma10']:>6.2f}  {row['ma20']:>6.2f}")

    print(f"\n📊 技术指标")
    print(f"  20日高/低: {snap['high20d']}/{snap['low20d']}  位置: {(snap['price']-snap['low20d'])/(snap['high20d']-snap['low20d'])*100:.0f}%")
    print(f"  30日涨/跌天数: {snap['up30']}/{snap['down30']}  大涨/大跌: +{snap['max_up']}%/{snap['max_down']}%")
    print(f"  量比: {snap['vol_ratio']}")

print(f"\n{'='*60}")
print("📊 技术面评分系统 （0-10分，越高越好）")
print(f"{'指标':<12}  {'华菱线缆':<10}  {'亚威股份':<10}  {'说明'}")
print("-" * 60)

all_scores = []
for s in snapshots:
    sc = calc_scores(s)
    all_scores.append(sc)

headers = ["趋势", "动量", "量价", "位置", "回调深度", "总分"]
for h in headers:
    v1 = all_scores[0][h]
    v2 = all_scores[1][h]
    bar1 = "█" * max(0, int(v1))
    bar2 = "█" * max(0, int(v2))
    print(f"{h:<8}  {v1:>4.1f} {bar1:<10}  {v2:>4.1f} {bar2:<10}  {'上涨占比/均线' if h=='趋势' else '涨跌比/今日涨跌' if h=='动量' else '量价配合' if h=='量价' else '低位=高分' if h=='位置' else '超跌幅度' if h=='回调深度' else '综合'}")

print()

print(f"🧠 AI 综合评分 + 分析...\n")
prompt = f"""你是顶级A股量化分析师。基于以下两只股票的技术面数据，分别给出每只股票五个维度的评分（1-10分）和综合评分。

数据截止：{datetime.now().strftime('%Y-%m-%d')}

{json.dumps(snapshots, ensure_ascii=False, indent=2)}

已有技术面机器评分（供参考，可调整）：
{json.dumps(dict(zip([s["name"] for s in snapshots], all_scores)), ensure_ascii=False, indent=2)}

请按以下格式输出，不得缺项：

━━━ 评分结果 ━━━

【华菱线缆(001208)】
趋势评分: X/10 - 理由一句话
动量评分: X/10 - 理由一句话
量价评分: X/10 - 理由一句话
位置评分: X/10 - 理由一句话
风险评分: X/10 - 理由一句话（越低越危险）
综合评分: X/10
评分等级: ★★★★★/★★★★☆/★★★☆☆/★★☆☆☆/★☆☆☆☆
一句话总结：

【亚威股份(002559)】
趋势评分: X/10 - 理由一句话
动量评分: X/10 - 理由一句话
量价评分: X/10 - 理由一句话
位置评分: X/10 - 理由一句话
风险评分: X/10 - 理由一句话
综合评分: X/10
评分等级: ★★★★★/★★★★☆/★★★☆☆/★★☆☆☆/★☆☆☆☆
一句话总结：

━━━ 对比结论 ━━━
谁更强：理由
操作建议：
"""

resp = client.chat.completions.create(
    model=MODEL, messages=[{"role":"user","content":prompt}],
    temperature=0.2, max_tokens=3000
)
print(resp.choices[0].message.content)
