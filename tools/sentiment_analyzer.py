"""
情绪面分析模块
- 全局情绪（热门板块/热股/龙虎榜/北向资金）
- 个股情绪（人气/热度/龙虎榜/扫雷）
- 综合情绪评分
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import adata
import pandas as pd


class SentimentAnalyzer:
    """情绪面分析器"""

    def __init__(self):
        self._hot_stocks: pd.DataFrame | None = None
        self._hot_industry: pd.DataFrame | None = None
        self._hot_concept: pd.DataFrame | None = None
        self._billboard: pd.DataFrame | None = None
        self._mine_cache: dict[str, dict] = {}
        self._north_flow: pd.DataFrame | None = None
        self._hot_stock_codes: set[str] = set()
        self._billboard_codes: dict[str, dict] = {}

    def fetch_all(self) -> dict[str, Any]:
        """获取所有情绪面数据"""
        results = {}
        results["hot_industry"] = self._safe_fetch("热门行业", self._fetch_hot_industry)
        results["hot_concept"] = self._safe_fetch("热门概念", self._fetch_hot_concept)
        results["hot_stocks"] = self._safe_fetch("热股100", self._fetch_hot_stocks)
        results["billboard"] = self._safe_fetch("龙虎榜", self._fetch_billboard)
        results["north_flow"] = self._safe_fetch("北向资金", self._fetch_north_flow)
        results["margin"] = self._safe_fetch("融资融券", self._fetch_margin)
        results["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return results

    def _safe_fetch(self, name: str, fn):
        try:
            return fn()
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    def _fetch_hot_industry(self) -> dict:
        df = adata.sentiment.hot.hot_concept_20_ths(plate_type=2)
        if df is None or len(df) == 0:
            return {"status": "empty", "items": []}
        self._hot_industry = df
        items = []
        for _, row in df.iterrows():
            items.append({
                "rank": int(row["rank"]),
                "name": row["concept_name"],
                "change_pct": float(row["change_pct"]),
                "hot_value": float(row["hot_value"]),
                "tag": row["hot_tag"],
            })
        return {"status": "ok", "items": items}

    def _fetch_hot_concept(self) -> dict:
        df = adata.sentiment.hot.hot_concept_20_ths(plate_type=1)
        if df is None or len(df) == 0:
            return {"status": "empty", "items": []}
        self._hot_concept = df
        items = []
        for _, row in df.iterrows():
            items.append({
                "rank": int(row["rank"]),
                "name": row["concept_name"],
                "change_pct": float(row["change_pct"]),
                "hot_value": float(row["hot_value"]),
                "tag": row["hot_tag"],
            })
        return {"status": "ok", "items": items}

    def _fetch_hot_stocks(self) -> dict:
        df = adata.sentiment.hot.hot_rank_100_ths()
        if df is None or len(df) == 0:
            return {"status": "empty", "items": []}
        self._hot_stocks = df
        self._hot_stock_codes = set(df["stock_code"].astype(str).str.zfill(6))
        items = []
        for _, row in df.iterrows():
            items.append({
                "rank": int(row["rank"]),
                "code": str(row["stock_code"]).zfill(6),
                "name": row["short_name"],
                "change_pct": float(row["change_pct"]),
                "hot_value": float(row["hot_value"]),
                "pop_tag": row.get("pop_tag", ""),
                "concept_tag": str(row.get("concept_tag", "")),
            })
        return {"status": "ok", "items": items}

    def _fetch_billboard(self) -> dict:
        df = adata.sentiment.hot.list_a_list_daily()
        if df is None or len(df) == 0:
            return {"status": "empty", "items": []}
        self._billboard = df
        self._billboard_codes = {}
        items = []
        for _, row in df.iterrows():
            code = str(row["stock_code"]).zfill(6)
            entry = {
                "code": code,
                "name": row["short_name"],
                "close": float(row["close"]),
                "change_pct": float(row["change_cpt"]),
                "turnover_ratio": float(row["turnover_ratio"]),
                "net_amount": float(row["a_net_amount"]),
                "reason": row["reason"],
                "is_up": float(row["change_cpt"]) > 0,
            }
            items.append(entry)
            self._billboard_codes[code] = entry
        return {"status": "ok", "items": items}

    def _fetch_north_flow(self) -> dict:
        try:
            cur = adata.sentiment.north.north_flow_current()
            if cur is not None and len(cur) > 0:
                last = cur.iloc[-1]
                return {
                    "status": "ok",
                    "net_hgt": float(last["net_hgt"]),
                    "net_sgt": float(last["net_sgt"]),
                    "net_tgt": float(last["net_tgt"]),
                    "time": str(last["trade_time"]),
                }
        except Exception:
            pass
        try:
            hist = adata.sentiment.north.north_flow(start_date="2026-06-01")
            if hist is not None and len(hist) > 0:
                last = hist.iloc[-1]
                return {
                    "status": "ok",
                    "net_hgt": float(last["net_hgt"]),
                    "net_sgt": float(last["net_sgt"]),
                    "net_tgt": float(last["net_tgt"]),
                    "trade_date": str(last["trade_date"]),
                }
        except Exception:
            pass
        return {"status": "unavailable", "note": "北向资金API暂不可用"}

    def _fetch_margin(self) -> dict:
        try:
            df = adata.sentiment.securities_margin()  # type: ignore
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                return {
                    "status": "ok",
                    "trade_date": str(last["trade_date"]),
                    "rzye": float(last["rzye"]),
                    "rqye": float(last["rqye"]),
                    "rzrqye": float(last["rzrqye"]),
                }
        except Exception:
            pass
        return {"status": "unavailable", "note": "融资融券API暂不可用"}

    def get_stock_sentiment(self, code: str) -> dict:
        """获取单只股票的情绪面数据"""
        result = {"sentiment_score": 0, "details": []}
        code = code.zfill(6)

        # 1. 是否在热股100中
        if code in self._hot_stock_codes and self._hot_stocks is not None:
            row = self._hot_stocks[self._hot_stocks["stock_code"].astype(str).str.zfill(6) == code]
            if len(row) > 0:
                r = row.iloc[0]
                result["hot_rank"] = int(r["rank"])
                result["hot_value"] = float(r["hot_value"])
                result["concept_tag"] = str(r.get("concept_tag", ""))
                result["details"].append(f"同花顺热股第{result['hot_rank']}名")
                # 热度加分：排名越前加分越多
                score = max(0, 15 - int(r["rank"]) // 7)
                result["sentiment_score"] += score

        # 2. 是否在龙虎榜中
        if code in self._billboard_codes:
            bb = self._billboard_codes[code]
            result["billboard"] = bb
            direction = "涨停/大涨" if bb["is_up"] else "跌停/大跌"
            result["details"].append(f"今日龙虎榜({direction})")
            result["sentiment_score"] += 5 if bb["is_up"] else -3

        return result

    def get_mine_clearance(self, code: str) -> dict | None:
        """获取个股扫雷数据（带缓存）"""
        code = code.zfill(6)
        if code in self._mine_cache:
            return self._mine_cache[code]
        try:
            df = adata.sentiment.mine.mine_clearance_tdx(stock_code=code)
            if df is not None and len(df) > 0:
                result = {
                    "score": int(df.iloc[0]["score"]),
                    "risks": [],
                }
                for _, row in df.iterrows():
                    result["risks"].append({
                        "type": row["s_type"],
                        "reason": row["reason"],
                    })
                self._mine_cache[code] = result
                return result
        except Exception:
            pass
        self._mine_cache[code] = None
        return None

    def calc_sentiment_score(self, code: str) -> tuple[int, list[str]]:
        """计算个股情绪评分（0-20分）"""
        info = self.get_stock_sentiment(code)
        return info["sentiment_score"], info["details"]

    def calc_market_sentiment_score(self, hot_industry: dict, hot_stocks: dict) -> dict:
        """计算大盘整体情绪评分"""
        scores = {}
        reasons = []

        # 从行业板块判断
        if hot_industry.get("status") == "ok":
            items = hot_industry["items"]
            top_gainers = [i for i in items if i["change_pct"] > 2]
            top_losers = [i for i in items if i["change_pct"] < -1]
            sector_score = len(top_gainers) * 3 - len(top_losers) * 2
            sector_score = max(-20, min(20, sector_score))
            scores["sector_momentum"] = sector_score
            if top_gainers:
                names = "、".join(i["name"] for i in top_gainers[:5])
                reasons.append(f"热门行业走强: {names}")
            if top_losers:
                names = "、".join(i["name"] for i in top_losers[:3])
                reasons.append(f"行业偏弱: {names}")

        # 从热股判断
        if hot_stocks.get("status") == "ok":
            items = hot_stocks["items"]
            up_count = sum(1 for i in items if i["change_pct"] > 0)
            hot_ratio = up_count / len(items) if items else 0.5
            hot_score = int((hot_ratio - 0.5) * 40)
            hot_score = max(-20, min(20, hot_score))
            scores["hot_stock_momentum"] = hot_score
            reasons.append(f"热股100中上涨占比{hot_ratio*100:.0f}%")

        total = sum(scores.values())
        if total > 15:
            level = "偏暖"
        elif total > 5:
            level = "中性偏暖"
        elif total > -5:
            level = "中性"
        elif total > -15:
            level = "中性偏冷"
        else:
            level = "偏冷"

        return {
            "score": total,
            "level": level,
            "details": scores,
            "reasons": reasons,
        }

    def generate_sentiment_report(self) -> str:
        """生成大盘情绪面报告 Markdown"""
        data = self.fetch_all()

        md = []
        md.append("# A股情绪面日报")
        md.append(f"\n生成时间：{data['timestamp']}\n")

        # ===== 总体情绪判断 =====
        sentiment = self.calc_market_sentiment_score(data["hot_industry"], data["hot_stocks"])
        md.append("## 总体情绪")
        md.append(f"\n- 情绪评分：**{sentiment['score']}**")
        md.append(f"- 情绪等级：**{sentiment['level']}**")
        for r in sentiment["reasons"]:
            md.append(f"- {r}")
        md.append("")

        # ===== 热门行业 =====
        md.append("---\n## 热门行业 TOP10\n")
        hi = data.get("hot_industry", {})
        if hi.get("status") == "ok":
            md.append("| 排名 | 行业 | 涨幅 | 热度值 | 状态 |")
            md.append("|---|---:|---:|---:|")
            for i in hi["items"]:
                arrow = "🟢" if i["change_pct"] > 0 else "🔴"
                md.append(f"| {i['rank']} | {i['name']} | {arrow}{i['change_pct']:+.2f}% | {i['hot_value']:.0f} | {i['tag']} |")
        else:
            md.append(f"数据暂不可用: {hi.get('error', '')}")
        md.append("")

        # ===== 热门概念 =====
        md.append("---\n## 热门概念 TOP10\n")
        hc = data.get("hot_concept", {})
        if hc.get("status") == "ok":
            md.append("| 排名 | 概念 | 涨幅 | 热度值 | 状态 |")
            md.append("|---|---:|---:|---:|")
            for i in hc["items"]:
                arrow = "🟢" if i["change_pct"] > 0 else "🔴"
                md.append(f"| {i['rank']} | {i['name']} | {arrow}{i['change_pct']:+.2f}% | {i['hot_value']:.0f} | {i['tag']} |")
        else:
            md.append(f"数据暂不可用: {hc.get('error', '')}")
        md.append("")

        # ===== 热股100 TOP20 =====
        md.append("---\n## 同花顺热股 TOP20\n")
        hs = data.get("hot_stocks", {})
        if hs.get("status") == "ok":
            md.append("| 排名 | 名称 | 代码 | 涨幅 | 热度值 | 标签 | 概念 |")
            md.append("|---|---:|---:|---:|---:|")
            for i in hs["items"][:20]:
                arrow = "🟢" if i["change_pct"] > 0 else "🔴"
                tag = i.get("pop_tag", "") or "-"
                concept = i.get("concept_tag", "") or "-"
                if len(concept) > 20:
                    concept = concept[:20] + "..."
                md.append(f"| {i['rank']} | {i['name']} | {i['code']} | {arrow}{i['change_pct']:+.2f}% | {i['hot_value']:.0f} | {tag} | {concept} |")
        else:
            md.append(f"数据暂不可用: {hs.get('error', '')}")
        md.append("")

        # ===== 北向资金 =====
        md.append("---\n## 北向资金\n")
        nf = data.get("north_flow", {})
        if nf.get("status") == "ok":
            net = nf.get("net_tgt", 0)
            arrow = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
            md.append(f"{arrow} 合计净买入：**{net/1e8:+.2f}亿**")
            md.append(f"- 沪港通：{nf.get('net_hgt', 0)/1e8:+.2f}亿")
            md.append(f"- 深港通：{nf.get('net_sgt', 0)/1e8:+.2f}亿")
        else:
            md.append(f"数据暂不可用")
        md.append("")

        # ===== 龙虎榜概要 =====
        md.append("---\n## 今日龙虎榜\n")
        bb = data.get("billboard", {})
        if bb.get("status") == "ok":
            items = bb["items"]
            up_items = [i for i in items if i["is_up"]]
            down_items = [i for i in items if not i["is_up"]]
            md.append(f"- 上榜总数：**{len(items)}** 只")
            md.append(f"- 上涨：{len(up_items)} 只 | 下跌：{len(down_items)} 只\n")
            md.append("### 机构净买入 TOP5")
            sorted_up = sorted(up_items, key=lambda x: x["net_amount"], reverse=True)
            md.append("| 名称 | 代码 | 涨幅 | 净买入(万) | 原因 |")
            md.append("|---|---:|---:|---:|")
            for i in sorted_up[:5]:
                md.append(f"| {i['name']} | {i['code']} | +{i['change_pct']:.2f}% | {i['net_amount']/1e4:.0f} | {i['reason'][:20]} |")
            md.append("\n### 机构净卖出 TOP3")
            sorted_down = sorted(down_items, key=lambda x: x["net_amount"])
            md.append("| 名称 | 代码 | 涨幅 | 净卖出(万) | 原因 |")
            md.append("|---|---:|---:|---:|")
            for i in sorted_down[:3]:
                md.append(f"| {i['name']} | {i['code']} | {i['change_pct']:.2f}% | {abs(i['net_amount'])/1e4:.0f} | {i['reason'][:20]} |")
        else:
            md.append(f"数据暂不可用: {bb.get('error', '')}")

        return "\n".join(md)
