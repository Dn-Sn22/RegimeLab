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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/main.log")
    ]
)
log = logging.getLogger(__name__)

current_trade_signal = {"signal": "neutral", "confidence": 0.0}
last_anomaly_flag    = False
last_entry_time      = 0.0
ENTRY_COOLDOWN       = 30

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
    log.info(f"Сделка записана | {signal.upper()} @ ${price:,.2f}")


async def research_task():
    global current_trade_signal

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info("Research: получаем новости...")
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

                trade_signal         = aggregate_signals(signals, fg)
                current_trade_signal = trade_signal

                log.info(
                    f"Research итог: {trade_signal['signal'].upper()} | "
                    f"Уверенность: {trade_signal['confidence']} | "
                    f"Fear&Greed: {fg['value']} ({fg['label']})"
                )

            except Exception as e:
                log.error(f"Research ошибка: {e}")

            await asyncio.sleep(300)


async def scanner_task():
    global last_anomaly_flag, prices, volumes, last_entry_time

    WS_MARKET_URL = "wss://stream.binance.com:9443"
    SYMBOL        = config.SYMBOL.lower()
    WS_URL        = f"{WS_MARKET_URL}/ws/{SYMBOL}@trade"

    log.info(f"Scanner: подключаемся к {WS_URL}")

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
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
                            result = await execute_signal(signal, decision, state)
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

        except Exception as e:
            log.error(f"Scanner ошибка: {e} | Переподключение через 5 сек...")
            await asyncio.sleep(5)


async def status_task():
    while True:
        await asyncio.sleep(30)
        state  = load_state()
        signal = current_trade_signal

        log.info(
            f"[СТАТУС] "
            f"Баланс: ${state.balance:.2f} | "
            f"Позиций: {state.open_positions} | "
            f"Сделок: {state.total_trades} | "
            f"Сигнал: {signal['signal'].upper()} | "
            f"Аномалия: {last_anomaly_flag}"
        )


async def main():
    log.info("=" * 50)
    log.info("BTC Trading Bot запущен")
    log.info(f"Режим: {config.MODE} | Символ: {config.SYMBOL}")
    log.info("=" * 50)

    state = load_state()
    state.open_positions = 0
    save_state(state)
    log.info(f"Позиции сброшены | Баланс: ${state.balance:.2f}")

    await asyncio.gather(
        research_task(),
        scanner_task(),
        status_task()
    )


if __name__ == "__main__":
    asyncio.run(main())