# main.py
import asyncio
import logging
import json
from datetime import datetime
from pathlib import Path

import websockets
import aiohttp

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

from src.scanner import detect_anomaly, prices, volumes, process_tick
from src.research import aggregate_signals
from src.research import fetch_cryptopanic, fetch_fear_greed, fetch_rss, analyze_with_claude
from src.risk import load_state, save_state, check_risk
from src.executor import execute_signal
from src.telegram_bot import notify_signal, notify_startup, notify_cryptopanic_disabled

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/main.log")
    ]
)
log = logging.getLogger(__name__)

current_trade_signal = {"signal": "neutral", "confidence": 0.0, "fear_greed": {"value": 50, "label": "Neutral"}}
last_anomaly_flag    = False
last_entry_time      = 0.0
last_telegram_time   = 0.0
ENTRY_COOLDOWN       = 1200
TELEGRAM_COOLDOWN    = 1800

TRADES_FILE = Path("logs/trades.xlsx")


def log_trade(
    signal: str,
    price: float,
    position_size: float,
    stop_loss: float,
    confidence: float,
    z_score: float,
    order_id: str
):
    import openpyxl

    xlsx_file = Path("logs/trades.xlsx")

    if xlsx_file.exists():
        wb = openpyxl.load_workbook(xlsx_file)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "trades"
        ws.append([
            "timestamp", "signal", "price_entry",
            "position_size", "stop_loss", "confidence",
            "z_score", "order_id"
        ])

    ws.append([
        datetime.utcnow().isoformat(),
        signal,
        round(price, 2),
        round(position_size, 2),
        round(stop_loss, 2),
        round(confidence, 2),
        round(z_score, 2),
        order_id
    ])

    wb.save(xlsx_file)
    log.info(f"The deal is saved | {signal.upper()} @ ${price:,.2f}")


async def research_task():
    global current_trade_signal

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info("Research: getting news...")
                news1 = await fetch_cryptopanic(session)
                news2 = await fetch_rss(session)
                fg    = await fetch_fear_greed(session)

                all_news = news1 + news2
                signals  = []

                for item in all_news:
                    signal = await analyze_with_claude(
                        session, item["title"], item["content"], item["source"]
                    )
                    if signal:
                        signals.append(signal)

                trade_signal             = aggregate_signals(signals, fg)
                trade_signal["fear_greed"] = fg
                current_trade_signal     = trade_signal

                log.info(
                    f"Research result: {trade_signal['signal'].upper()} | "
                    f"Confidence: {trade_signal['confidence']} | "
                    f"Fear&Greed: {fg['value']} ({fg['label']})"
                )

            except Exception as e:
                log.error(f"Research error: {e}")

            await asyncio.sleep(300)


async def scanner_task():
    global last_anomaly_flag, prices, volumes, last_entry_time, last_telegram_time

    WS_MARKET_URL = "wss://stream.binance.com:9443"
    SYMBOL        = config.SYMBOL.lower()
    WS_URL        = f"{WS_MARKET_URL}/ws/{SYMBOL}@trade"

    log.info(f"Scanner: подключаемся к {WS_URL}")

    while True:
        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=10
            ) as ws:
                log.info("Scanner: WebSocket подключён")
                async for message in ws:
                    data  = json.loads(message)
                    await process_tick(data)

                    price             = float(data["p"])
                    anomaly, z, chg   = detect_anomaly(price)
                    last_anomaly_flag = anomaly

                    if anomaly and current_trade_signal["signal"] != "neutral":

                        now_t = asyncio.get_event_loop().time()
                        if now_t - last_entry_time < ENTRY_COOLDOWN:
                            continue

                        last_entry_time = now_t
                        signal     = current_trade_signal["signal"]
                        confidence = current_trade_signal["confidence"]

                        log.info(
                            f"СИГНАЛ ВХОДА | {signal.upper()} | "
                            f"Z: {z:+.2f} | Confidence: {confidence}"
                        )

                        state    = load_state()
                        decision = check_risk(
                            state=state,
                            signal=signal,
                            confidence=confidence,
                            current_price=price
                        )

                        if decision.allowed:
                            # ← передаём цену из WebSocket, без лишнего REST запроса
                            result = await execute_signal(signal, decision, state, price=price)
                            if result and result.success:
                                log_trade(
                                    signal=signal,
                                    price=result.price,
                                    position_size=result.usdt_value,
                                    stop_loss=decision.stop_loss,
                                    confidence=confidence,
                                    z_score=z,
                                    order_id=result.order_id
                                )
                        else:
                            log.warning(f"Риск отклонил: {decision.reason}")

                        # Telegram regardless of position - once an hour
                        now_tg = asyncio.get_event_loop().time()
                        if now_tg - last_telegram_time >= TELEGRAM_COOLDOWN:
                            last_telegram_time = now_tg
                            await notify_signal(
                                signal=signal,
                                price=price,
                                stop_loss=decision.stop_loss,
                                z_score=z,
                                confidence=confidence,
                                fear_greed_value=current_trade_signal["fear_greed"]["value"],
                                fear_greed_label=current_trade_signal["fear_greed"]["label"]
                            )

        except Exception as e:
            log.error(f"Scanner error: {e} | Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


async def status_task():
    while True:
        await asyncio.sleep(30)
        state  = load_state()
        signal = current_trade_signal

        log.info(
            f"[STATUS] "
            f"Balance: ${state.balance:.2f} | "
            f"Positions: {state.open_positions} | "
            f"Deals: {state.total_trades} | "
            f"Signal: {signal['signal'].upper()} | "
            f"Anomaly: {last_anomaly_flag}"
        )


async def main():
    log.info("=" * 50)
    log.info("BTC Trading Bot active")
    log.info(f"Mode: {config.MODE} | Symbol: {config.SYMBOL}")
    log.info("=" * 50)

    state = load_state()
    state.open_positions = 0
    save_state(state)
    log.info(f"Positions reset | Balance: ${state.balance:.2f}")

    await notify_startup(config.MODE, state.balance)

    await asyncio.gather(
        research_task(),
        scanner_task(),
        status_task()
    )


if __name__ == "__main__":
    asyncio.run(main())
