"""
持仓分析工具 - 基于 adata + AI API
"""
import adata
import pandas as pd
from datetime import datetime, timedelta
from openai import OpenAI
import json
import sys

# ===== 配置区 =====
API_KEY = "sk-cjbpzqvq009y2kUH3Bc6ZRYdGufPJnH2b4cDkzLIzAeQ4CKi"
API_BASE = "https://aihub.firstshare.cn/v1"
MODEL = "deepseek-v4-pro-max"
# =================

client = OpenAI(api_key=API_KEY, base_url=API_BASE)

def get_stock_snapshot(code, name):
    """获取单只股票快照"""
    m = adata.stock.market.get_market(stock_code=code, k_type=1, start_date=(datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d'))
    if m is None or len(m) == 0:
        return None
    
    df = m[['trade_date','open','close','high','low','volume','amount','change_pct']].copy()
    df['ma5'] = df['close'].rolling(5).mean().round(2)
    df['ma10'] = df['close'].rolling(10).mean().round(2)
    df['ma20'] = df['close'].rolling(20).mean().round(2)
    
    last = df.iloc[-1]
    last30 = df.tail(30)
    flow = adata.stock.market.get_capital_flow(stock_code=code)
    
    return {
        'name': name, 'code': code,
        'price': float(last['close']), 'chg': float(last['change_pct']),
        'high20d': float(df['high'].tail(20).max()), 'low20d': float(df['low'].tail(20).min()),
        'ma5': float(last['ma5']), 'ma10': float(last['ma10']), 'ma20': float(last['ma20']),
        'up30': int((last30['change_pct']>0).sum()),
        'down30': int((last30['change_pct']<0).sum()),
        'max_up': float(last30['change_pct'].max()),
        'max_down': float(last30['change_pct'].min()),
        'vol_ratio': round(float(last['volume'] / df['volume'].tail(5).mean()), 2),
        'main_flow_5d': round(float(flow.tail(5)['main_net_inflow'].sum()/1e4), 1) if flow is not None else 0,
        'main_flow_10d': round(float(flow.tail(10)['main_net_inflow'].sum()/1e4), 1) if flow is not None else 0,
    }

def ai_judge(stocks_data):
    """AI分析"""
    prompt = f"""你是一对一投资顾问。用户持仓如下，请给出次日操作建议。

数据截止：{datetime.now().strftime('%Y-%m-%d')}
大背景：SpaceX今晚纳斯达克上市(SPCX)，募资750亿美元，史上最大IPO

{json.dumps(stocks_data, ensure_ascii=False, indent=2)}

请按以下格式输出每个股票：

【股票名】
- 现状判断：一句话
- 明日走势预判：涨/跌/震荡 概率+理由
- 操作建议：持有/减仓/清仓/加仓 + 具体价位
- 止损位：
- 止盈位：
"""
    
    resp = client.chat.completions.create(
        model=MODEL, messages=[{'role':'user','content':prompt}],
        temperature=0.2, max_tokens=2000
    )
    return resp.choices[0].message.content

if __name__ == '__main__':
    portfolio = [
        ('002251', '步步高'),
    ]
    
    print("🔍 获取持仓数据...")
    data = []
    for code, name in portfolio:
        s = get_stock_snapshot(code, name)
        if s:
            data.append(s)
            print(f"  ✅ {name}({code}) {s['price']}元 今日{s['chg']}%")
    
    print("\n🧠 AI分析中...\n")
    result = ai_judge(data)
    print(result)
