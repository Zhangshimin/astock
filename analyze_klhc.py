import json
from datetime import datetime
from openai import OpenAI

client = OpenAI(
    api_key="sk-cjbpzqvq009y2kUH3Bc6ZRYdGufPJnH2b4cDkzLIzAeQ4CKi",
    base_url="https://aihub.firstshare.cn/v1"
)

snap = {
    "name": "康龙化成",
    "code": "300759",
    "price": 22.47,
    "chg": 0.40,
    "volume": 42830000,
    "high20d": 27.37,
    "low20d": 21.01,
    "ma5": 22.21,
    "ma10": 22.67,
    "ma20": 24.18,
    "up30": 9,
    "down30": 21,
    "max_up": 3.04,
    "max_down": -4.16,
    "vol_5d_avg": 36780000,
}

prompt = f"""你是专业股票分析师。分析以下股票数据并给出建议。

数据截止：{datetime.now().strftime('%Y-%m-%d')}
大盘背景：今日沪指+1.16%，创业板+3.66%，科技+有色领涨，医药生物-0.68%

数据：
{json.dumps(snap, ensure_ascii=False, indent=2)}

按此格式输出：
【康龙化成】
- 现状判断：
- 技术面分析（均线、量价、趋势）：
- 关键位置（支撑/压力）：
- 明日走势预判：
- 操作建议：
- 止损位：
- 止盈位：
"""

resp = client.chat.completions.create(
    model="deepseek-v4-pro-max",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.2,
    max_tokens=2000
)
print(resp.choices[0].message.content)
