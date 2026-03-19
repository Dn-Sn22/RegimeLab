import asyncio
import logging
import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import List

import aiohttp
import anthropic
import feedparser

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

log = logging.getLogger(__name__)

CRYPTOPANIC_URL   = "https://cryptopanic.com/api/developer/v2/posts/"
FEAR_GREED_URL    = "https://api.alternative.me/fng/"
COINTELEGRAPH_RSS = "https://cointelegraph.com/rss"
COINDESK_RSS      = "https://www.coindesk.com/arc/outboundfeeds/rss/"
FETCH_INTERVAL    = 300
MAX_NEWS          = 5

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

cryptopanic_available  = True
cryptopanic_reset_hour = None


@dataclass
class NewsSignal:
    title:        str
    sentiment:    str
    summary:      str
    price_impact: str
    source:       str
    timestamp:    datetime
    coins:        List[str] = field(default_factory=list)
    confidence:   float = 0.0


async def analyze_with_claude(
    session: aiohttp.ClientSession,
    title: str,
    content: str,
    source: str
) -> NewsSignal | None:
    prompt = f"""Analyze this crypto news for a BTC/USDT trading bot.

Title: {title}
Content: {content}

Return ONLY a valid JSON object, no other text, no markdown:
{{
    "sentiment": "bullish" or "bearish" or "neutral",
    "price_impact": "high" or "medium" or "low",
    "summary": "1-2 sentences in English",
    "coins": ["BTC"],
    "confidence": 0.85
}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        return NewsSignal(
            title=title,
            sentiment=data["sentiment"],
            summary=data["summary"],
            price_impact=data["price_impact"],
            source=source,
            timestamp=datetime.utcnow(),
            coins=data.get("coins", ["BTC"]),
            confidence=float(data.get("confidence", 0.0))
        )
    except Exception as e:
        log.error(f"Claude анализ ошибка: {e}")
        return None


async def fetch_cryptopanic(session: aiohttp.ClientSession) -> list[dict]:
    global cryptopanic_available, cryptopanic_reset_hour

    if not cryptopanic_available:
        current_hour = datetime.utcnow().hour
        if cryptopanic_reset_hour is not None and current_hour == 0:
            cryptopanic_available = True
            cryptopanic_reset_hour = None
            log.info("CryptoPanic: пробуем переподключиться — новый день")
        else:
            log.info("CryptoPanic: лимит исчерпан — пропускаем")
            return []

    params = {
        "auth_token": os.getenv("CRYPTOPANIC_API_KEY"),
        "currencies": "BTC",
        "kind":       "news",
        "filter":     "hot"
    }
    try:
        async with session.get(CRYPTOPANIC_URL, params=params) as resp:
            if resp.status == 429:
                cryptopanic_available = False
                cryptopanic_reset_hour = datetime.utcnow().hour
                log.warning("CryptoPanic: лимит 429 — автоотключение")
                return []
            resp.raise_for_status()
            data = await resp.json()
            results = data.get("results", [])[:MAX_NEWS]
            return [
                {
                    "title":   item["title"],
                    "content": item.get("body", item["title"]),
                    "source":  "cryptopanic"
                }
                for item in results
            ]
    except Exception as e:
        log.error(f"CryptoPanic ошибка: {e}")
        return []


async def fetch_fear_greed(session: aiohttp.ClientSession) -> dict:
    try:
        async with session.get(FEAR_GREED_URL) as resp:
            resp.raise_for_status()
            data = await resp.json()
            item = data["data"][0]
            return {
                "value":     int(item["value"]),
                "label":     item["value_classification"],
                "timestamp": datetime.utcnow()
            }
    except Exception as e:
        log.error(f"Fear & Greed ошибка: {e}")
        return {"value": 50, "label": "Neutral", "timestamp": datetime.utcnow()}


async def fetch_rss(session: aiohttp.ClientSession) -> list[dict]:
    results = []
    for url, source in [
        (COINTELEGRAPH_RSS, "cointelegraph"),
        (COINDESK_RSS, "coindesk")
    ]:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_NEWS]:
                results.append({
                    "title":   entry.title,
                    "content": entry.get("summary", entry.title),
                    "source":  source
                })
        except Exception as e:
            log.error(f"{source} RSS ошибка: {e}")
    return results


def aggregate_signals(signals: list[NewsSignal], fear_greed: dict) -> dict:
    if not signals:
        return {"signal": "neutral", "confidence": 0.0, "fear_greed": fear_greed}

    strong = [s for s in signals if s.confidence >= 0.7] or signals

    bullish = sum(1 for s in strong if s.sentiment == "bullish")
    bearish = sum(1 for s in strong if s.sentiment == "bearish")
    total   = len(strong)

    avg_confidence = sum(s.confidence for s in strong) / total

    if bullish > bearish:
        signal = "bullish"
    elif bearish > bullish:
        signal = "bearish"
    else:
        signal = "neutral"

    fg_value = fear_greed["value"]
    if fg_value < 25 and signal == "bearish":
        signal = "neutral"
    elif fg_value > 75 and signal == "bullish":
        signal = "neutral"

    return {
        "signal":     signal,
        "confidence": round(avg_confidence, 2),
        "bullish":    bullish,
        "bearish":    bearish,
        "total":      total,
        "fear_greed": fear_greed
    }


async def main_loop():
    log.info("Research агент запущен")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info("Research: получаем новости...")

                news1 = await fetch_cryptopanic(session)
                news2 = await fetch_rss(session)
                fg    = await fetch_fear_greed(session)

                all_news = news1 + news2
                log.info(
                    f"Источники: CryptoPanic={len(news1)} | "
                    f"RSS={len(news2)} | "
                    f"Fear&Greed: {fg['value']} ({fg['label']})"
                )

                signals = []
                for item in all_news:
                    signal = await analyze_with_claude(
                        session, item["title"], item["content"], item["source"]
                    )
                    if signal:
                        signals.append(signal)
                        log.info(
                            f"[{signal.source}] {signal.sentiment.upper()} "
                            f"(conf: {signal.confidence}) | {signal.title[:60]}..."
                        )

                trade_signal = aggregate_signals(signals, fg)
                log.info(
                    f"ИТОГ: {trade_signal['signal'].upper()} | "
                    f"Уверенность: {trade_signal['confidence']} | "
                    f"Bullish: {trade_signal['bullish']} Bearish: {trade_signal['bearish']}"
                )

            except Exception as e:
                log.error(f"Research ошибка: {e}")

            await asyncio.sleep(FETCH_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/research.log")
        ]
    )
    asyncio.run(main_loop())