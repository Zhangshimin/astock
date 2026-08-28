from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


TEXT_FIELDS = ("comment_content", "content", "text", "body", "description")
TITLE_FIELDS = ("post_title", "title", "subject")
DATE_FIELDS = ("created_at", "comment_date", "date", "time", "publish_time")
LIKE_FIELDS = ("comment_like", "like_count", "likes", "praise_count")
REPLY_FIELDS = ("reply_count", "comment_count", "replies")
AUTHOR_FIELDS = ("user_name", "author", "nickname", "user")
URL_FIELDS = ("url", "post_url", "link")

POSITIVE_TERMS = (
    "看好", "买入", "增持", "上涨", "涨停", "突破", "低估", "利好", "超预期",
    "业绩增长", "主升", "反弹", "强势", "机会", "加仓", "抄底", "放量", "新高",
)
NEGATIVE_TERMS = (
    "看空", "卖出", "减持", "下跌", "跌停", "破位", "高估", "利空", "低于预期",
    "亏损", "暴雷", "爆雷", "退市", "套牢", "风险", "出货", "割肉", "跑路", "诱多",
)
NEGATIONS = ("不", "没", "无", "未", "别", "莫")


@dataclass(frozen=True)
class ForumRecord:
    text: str
    title: str = ""
    source: str = "unknown"
    created_at: str = ""
    likes: int = 0
    replies: int = 0
    author: str = ""
    url: str = ""


@dataclass(frozen=True)
class ScoredRecord:
    record: ForumRecord
    score: float
    label: str
    matched_terms: tuple[str, ...]
    weight: float


@dataclass(frozen=True)
class SentimentSummary:
    total_loaded: int
    total_analyzed: int
    duplicates_removed: int
    bullish: int
    neutral: int
    bearish: int
    score: float
    confidence: float
    records: tuple[ScoredRecord, ...]


def _first_value(data: dict[str, Any], fields: Iterable[str]) -> Any:
    for field in fields:
        value = data.get(field)
        if value not in (None, ""):
            return value
    return ""


def _to_int(value: Any) -> int:
    try:
        return max(0, int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return 0


def normalize_record(data: dict[str, Any], source: str) -> ForumRecord | None:
    text = str(_first_value(data, TEXT_FIELDS)).strip()
    title = str(_first_value(data, TITLE_FIELDS)).strip()
    if not text and title:
        text, title = title, ""
    if len(re.sub(r"\s+", "", text)) < 4:
        return None
    return ForumRecord(
        text=text,
        title=title,
        source=source,
        created_at=str(_first_value(data, DATE_FIELDS)),
        likes=_to_int(_first_value(data, LIKE_FIELDS)),
        replies=_to_int(_first_value(data, REPLY_FIELDS)),
        author=str(_first_value(data, AUTHOR_FIELDS)),
        url=str(_first_value(data, URL_FIELDS)),
    )


def _load_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "records", "comments"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_records(path: Path, source: str = "auto") -> list[ForumRecord]:
    if not path.is_file():
        raise FileNotFoundError(path)
    detected_source = source if source != "auto" else _detect_source(path)
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = _load_jsonl(path)
    elif suffix == ".json":
        rows = _load_json(path)
    elif suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    elif suffix == ".txt":
        rows = [{"text": line} for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    else:
        raise ValueError(f"Unsupported input format: {suffix}")
    return [record for row in rows if (record := normalize_record(row, detected_source))]


def _detect_source(path: Path) -> str:
    lowered = str(path).lower()
    if "xueqiu" in lowered or "stock_posts" in lowered:
        return "xueqiu"
    if "eastmoney" in lowered or "guba" in lowered or "comment_" in lowered:
        return "eastmoney"
    return "unknown"


def _dedup_key(text: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", text.lower())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _term_polarity(text: str, term: str, polarity: float) -> float:
    total = 0.0
    start = 0
    while (index := text.find(term, start)) >= 0:
        prefix = text[max(0, index - 2):index]
        total += -polarity if any(prefix.endswith(negation) for negation in NEGATIONS) else polarity
        start = index + len(term)
    return total


def score_record(record: ForumRecord) -> ScoredRecord:
    text = f"{record.title} {record.text}"
    matched: list[str] = []
    raw_score = 0.0
    for term in POSITIVE_TERMS:
        if term in text:
            matched.append(f"+{term}")
            raw_score += _term_polarity(text, term, 1.0)
    for term in NEGATIVE_TERMS:
        if term in text:
            matched.append(f"-{term}")
            raw_score += _term_polarity(text, term, -1.0)
    score = math.tanh(raw_score / 2.0)
    label = "bullish" if score >= 0.2 else "bearish" if score <= -0.2 else "neutral"
    engagement = record.likes + record.replies
    weight = 1.0 + min(math.log1p(engagement), 4.0) * 0.15
    return ScoredRecord(record, round(score, 4), label, tuple(matched), round(weight, 4))


def _unique_records(records: list[ForumRecord]) -> list[ForumRecord]:
    seen: set[str] = set()
    unique: list[ForumRecord] = []
    for record in records:
        key = _dedup_key(record.text)
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def _summarize(records: list[ForumRecord], scored: tuple[ScoredRecord, ...]) -> SentimentSummary:
    total_weight = sum(item.weight for item in scored)
    aggregate = sum(item.score * item.weight for item in scored) / total_weight if total_weight else 0.0
    matched_count = sum(bool(item.matched_terms) for item in scored)
    coverage = matched_count / len(scored) if scored else 0.0
    confidence = min(1.0, math.sqrt(len(scored)) / 10.0) * min(1.0, coverage * 2.0)
    return SentimentSummary(
        total_loaded=len(records),
        total_analyzed=len(scored),
        duplicates_removed=len(records) - len(scored),
        bullish=sum(item.label == "bullish" for item in scored),
        neutral=sum(item.label == "neutral" for item in scored),
        bearish=sum(item.label == "bearish" for item in scored),
        score=round(aggregate * 100, 2),
        confidence=round(confidence * 100, 2),
        records=scored,
    )


def analyze_records(records: list[ForumRecord]) -> SentimentSummary:
    unique = _unique_records(records)
    return _summarize(records, tuple(score_record(record) for record in unique))


def _extract_json_content(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return payload


def _llm_batch(
    records: list[ForumRecord],
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
) -> list[ScoredRecord]:
    inputs = [
        {"id": index, "text": f"{record.title} {record.text}".strip()[:800]}
        for index, record in enumerate(records)
    ]
    prompt = (
        "你是A股论坛舆情分类器。结合中文金融语境、反讽、否定和喊单倾向判断每条文本。"
        "只输出JSON对象，格式为{\"items\":[{\"id\":0,\"score\":0.0,\"reason\":\"简短理由\"}]}。"
        "score取-1到1，-1强烈看空，0中性，1强烈看多。不得给出投资建议。\n"
        f"输入：{json.dumps(inputs, ensure_ascii=False)}"
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你负责结构化分析A股论坛情绪。"},
                {"role": "user", "content": prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = result["choices"][0]["message"]["content"]
    items = _extract_json_content(content).get("items", [])
    by_id = {int(item["id"]): item for item in items if isinstance(item, dict) and "id" in item}
    scored: list[ScoredRecord] = []
    for index, record in enumerate(records):
        item = by_id.get(index)
        if item is None:
            scored.append(score_record(record))
            continue
        score = max(-1.0, min(1.0, float(item.get("score", 0.0))))
        label = "bullish" if score >= 0.2 else "bearish" if score <= -0.2 else "neutral"
        engagement = record.likes + record.replies
        weight = 1.0 + min(math.log1p(engagement), 4.0) * 0.15
        reason = str(item.get("reason", "LLM classification")).strip()
        scored.append(ScoredRecord(record, round(score, 4), label, (reason,), round(weight, 4)))
    return scored


def analyze_records_with_llm(
    records: list[ForumRecord],
    api_key: str,
    base_url: str,
    model: str,
    batch_size: int = 20,
    timeout: int = 60,
) -> SentimentSummary:
    if not api_key:
        raise ValueError("ASTOCK_LLM_API_KEY is required for the LLM engine")
    unique = _unique_records(records)
    scored: list[ScoredRecord] = []
    for start in range(0, len(unique), batch_size):
        scored.extend(_llm_batch(unique[start:start + batch_size], api_key, base_url, model, timeout))
    return _summarize(records, tuple(scored))


def _display_text(item: ScoredRecord, limit: int = 100) -> str:
    text = re.sub(r"\s+", " ", item.record.text).strip().replace("|", "\\|")
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


def render_markdown(symbol: str, summary: SentimentSummary, input_path: Path) -> str:
    analyzed = max(summary.total_analyzed, 1)
    top_bullish = sorted((item for item in summary.records if item.score > 0), key=lambda item: item.score * item.weight, reverse=True)[:5]
    top_bearish = sorted((item for item in summary.records if item.score < 0), key=lambda item: item.score * item.weight)[:5]
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        f"# {symbol} 论坛情绪分析",
        "",
        f"- 生成时间：{generated_at}",
        f"- 输入文件：`{input_path}`",
        f"- 有效记录：{summary.total_analyzed}（原始 {summary.total_loaded}，去重 {summary.duplicates_removed}）",
        f"- 综合情绪：**{summary.score:+.2f}**（-100 看空，+100 看多）",
        f"- 置信度：**{summary.confidence:.2f}%**",
        "",
        "## 情绪分布",
        "",
        "| 看多 | 中性 | 看空 |",
        "|---:|---:|---:|",
        f"| {summary.bullish} ({summary.bullish / analyzed:.1%}) | {summary.neutral} ({summary.neutral / analyzed:.1%}) | {summary.bearish} ({summary.bearish / analyzed:.1%}) |",
        "",
        "## 代表性观点",
        "",
        "### 看多",
        "",
        "| 分值 | 来源 | 内容 |",
        "|---:|---|---|",
    ]
    lines.extend(f"| {item.score:+.2f} | {item.record.source} | {_display_text(item)} |" for item in top_bullish)
    if not top_bullish:
        lines.append("| - | - | 暂无明确看多样本 |")
    lines.extend(["", "### 看空", "", "| 分值 | 来源 | 内容 |", "|---:|---|---|"])
    lines.extend(f"| {item.score:+.2f} | {item.record.source} | {_display_text(item)} |" for item in top_bearish)
    if not top_bearish:
        lines.append("| - | - | 暂无明确看空样本 |")
    lines.extend([
        "",
        "## 使用边界",
        "",
        "当前为可解释的词典基线，尚不能可靠识别反讽、水军和复杂上下文。情绪结果只能作为投研因子，不能单独作为交易信号。",
        "",
    ])
    return "\n".join(lines)
