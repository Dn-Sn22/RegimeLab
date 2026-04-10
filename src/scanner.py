import asyncio
import json
import logging
import numpy as np
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import websockets
import urllib.request

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

log = logging.getLogger(__name__)

# Parameters 

SYMBOL         = config.SYMBOL.lower()
INTERVAL       = "3m"
WS_URL         = f"wss://stream.binance.com:9443/ws/{SYMBOL}@kline_{INTERVAL}"

HISTORY_SIZE   = 100     # candles for Z-score rolling window
Z_MIN_BARS     = 20      # minimum candles before Z-score is valid
Z_THRESHOLD    = 1.5     # Z-score anomaly threshold

RSI_PERIOD     = 14      # RSI period
RSI_OVERSOLD   = 40      # RSI below this -> bullish filter passes
RSI_OVERBOUGHT = 60      # RSI above this -> bearish filter passes

EMA_FAST       = 9       # fast EMA period
EMA_SLOW       = 21      # slow EMA period

VOLUME_MULT    = 1.5     # current volume must be > VOLUME_MULT * avg volume

PRINT_EVERY    = 1       # print status every N closed candles


# State 

closes  = deque(maxlen=HISTORY_SIZE)
volumes = deque(maxlen=HISTORY_SIZE)

last_candle_time = 0


# Indicators

def compute_zscore(close_arr: np.ndarray) -> float:
    """
    Z-score of the most recent log return vs rolling window.
    Same logic as before but on closed candles instead of ticks.
    """
    if len(close_arr) < Z_MIN_BARS:
        return 0.0

    returns = np.diff(np.log(close_arr))

    if len(returns) < 10:
        return 0.0

    mean_r = returns.mean()
    std_r  = returns.std()

    if std_r == 0:
        return 0.0

    recent_return = returns[-1]  # only last return, not avg of last 10
    return (recent_return - mean_r) / std_r


def compute_rsi(close_arr: np.ndarray, period: int = RSI_PERIOD) -> float:
    """Standard Wilder RSI."""
    if len(close_arr) < period + 1:
        return 50.0

    deltas = np.diff(close_arr[-(period + 1):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains.mean()
    avg_loss = losses.mean()

    if avg_loss == 0:
        return 100.0

    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def compute_ema(close_arr: np.ndarray, period: int) -> float:
    """Exponential Moving Average."""
    if len(close_arr) < period:
        return close_arr[-1] if len(close_arr) > 0 else 0.0

    k   = 2 / (period + 1)
    ema = close_arr[-period]

    for price in close_arr[-period + 1:]:
        ema = price * k + ema * (1 - k)

    return round(ema, 2)


def compute_volume_signal(vol_arr: np.ndarray) -> tuple[float, bool]:
    """
    Returns (avg_volume, is_volume_confirmed).
    Volume confirmed if current > avg * VOLUME_MULT.
    """
    if len(vol_arr) < 2:
        return 0.0, False

    avg_vol     = np.mean(list(vol_arr)[:-1])  # exclude current
    current_vol = vol_arr[-1]
    confirmed   = current_vol > avg_vol * VOLUME_MULT

    return round(avg_vol, 4), confirmed


# Signal Detection 

@dataclass
class CandleSignal:
    price:            float
    z_score:          float
    rsi:              float
    ema_fast:         float
    ema_slow:         float
    volume:           float
    avg_volume:       float
    anomaly:          bool
    volume_confirmed: bool
    trend:            str    # "bullish", "bearish", "neutral"
    signal:           str    # "bullish", "bearish", "neutral"
    filters_passed:   dict


def detect_signal(price: float, volume: float) -> CandleSignal:
    """
    Main signal detection on closed candle.
    Returns CandleSignal with all indicator values and final signal.
    """
    close_arr = np.array(closes)
    vol_arr   = np.array(volumes)

    z         = compute_zscore(close_arr)
    rsi       = compute_rsi(close_arr)
    ema_fast  = compute_ema(close_arr, EMA_FAST)
    ema_slow  = compute_ema(close_arr, EMA_SLOW)
    avg_vol, vol_confirmed = compute_volume_signal(vol_arr)

    # Anomaly detection
    anomaly = abs(z) >= Z_THRESHOLD

    # Trend direction from EMA
    if ema_fast > ema_slow:
        trend = "bullish"
    elif ema_fast < ema_slow:
        trend = "bearish"
    else:
        trend = "neutral"

    # Filter checks for BULLISH entry
    bullish_filters = {
        "z_score":  z >= Z_THRESHOLD,
        "rsi":      rsi <= RSI_OVERSOLD,
        "ema":      trend == "bullish",
        "volume":   vol_confirmed
    }

    # Filter checks for BEARISH entry
    bearish_filters = {
        "z_score":  z <= -Z_THRESHOLD,
        "rsi":      rsi >= RSI_OVERBOUGHT,
        "ema":      trend == "bearish",
        "volume":   vol_confirmed
    }

    # Signal requires ALL filters to pass
    if all(bullish_filters.values()):
        signal         = "bullish"
        filters_passed = bullish_filters
    elif all(bearish_filters.values()):
        signal         = "bearish"
        filters_passed = bearish_filters
    else:
        signal         = "neutral"
        filters_passed = bullish_filters if z >= 0 else bearish_filters

    return CandleSignal(
        price=price,
        z_score=round(z, 4),
        rsi=rsi,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        volume=volume,
        avg_volume=avg_vol,
        anomaly=anomaly,
        volume_confirmed=vol_confirmed,
        trend=trend,
        signal=signal,
        filters_passed=filters_passed
    )


def print_status(sig: CandleSignal):
    """Prints candle status to console."""
    now    = datetime.now().strftime("%H:%M:%S")
    trend  = sig.trend.upper()[:4]
    status = f"SIGNAL:{sig.signal.upper()}" if sig.signal != "neutral" else "ok"

    filters = (
        f"Z:{sig.z_score:+.2f} "
        f"RSI:{sig.rsi:.1f} "
        f"EMA:{trend} "
        f"Vol:{'OK' if sig.volume_confirmed else '--'}"
    )

    print(
        f"[{now}] "
        f"BTC: ${sig.price:,.2f} | "
        f"{filters} | "
        f"{status}"
    )


# WebSocket Processing 

async def process_kline(data: dict) -> CandleSignal | None:
    """
    Processes incoming kline message.
    Returns CandleSignal only on candle close, None otherwise.
    """
    global last_candle_time

    kline     = data["k"]
    is_closed = kline["x"]  # True when candle is closed

    price     = float(kline["c"])  # close price
    volume    = float(kline["v"])  # candle volume
    open_time = kline["t"]

    if not is_closed:
        return None

    if open_time == last_candle_time:
        return None

    last_candle_time = open_time

    closes.append(price)
    volumes.append(volume)

    if len(closes) < Z_MIN_BARS:
        log.info(f"Warming up | Candles: {len(closes)}/{Z_MIN_BARS}")
        return None

    sig = detect_signal(price, volume)

    print_status(sig)

    if sig.signal != "neutral":
        passed   = [k for k, v in sig.filters_passed.items() if v]
        failed   = [k for k, v in sig.filters_passed.items() if not v]
        log.warning(
            f"SIGNAL | {sig.signal.upper()} | "
            f"Price: ${sig.price:,.2f} | "
            f"Z: {sig.z_score:+.2f} | "
            f"RSI: {sig.rsi} | "
            f"EMA: {sig.trend} | "
            f"Vol: {'OK' if sig.volume_confirmed else 'LOW'} | "
            f"Passed: {passed} | "
            f"Failed: {failed}"
        )
    elif sig.anomaly:
        # Z-score triggered but other filters blocked entry
        failed = [k for k, v in sig.filters_passed.items() if not v]
        log.info(
            f"ANOMALY BLOCKED | Z: {sig.z_score:+.2f} | "
            f"Blocked by: {failed}"
        )

    return sig


# History Prefill 

def prefill_history():
    """
    Fetches last HISTORY_SIZE closed candles via REST API on startup
    or reconnect. Fills closes and volumes deques instantly so no warmup needed.
    """
    global closes, volumes

    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol={SYMBOL.upper()}"
        f"&interval={INTERVAL}"
        f"&limit={HISTORY_SIZE}"
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            import json as _json
            klines = _json.loads(resp.read().decode())

        # Each kline: [open_time, open, high, low, close, volume, ...]
        closes.clear()
        volumes.clear()

        for k in klines[:-1]:  # exclude last — it may not be closed yet
            closes.append(float(k[4]))
            volumes.append(float(k[5]))

        log.info(
            f"History prefilled | {len(closes)} candles loaded | "
            f"Last close: ${closes[-1]:,.2f}"
        )

    except Exception as e:
        log.error(f"Prefill error: {e} | Will warm up from live data")


# Standalone Runner 

async def main():
    log.info(f"Scanner started | {SYMBOL.upper()} | {INTERVAL} candles | {WS_URL}")

    while True:
        try:
            # Prefill history before connecting — instant warmup
            prefill_history()

            async with websockets.connect(
                WS_URL,
                ping_interval=20,
                ping_timeout=10
            ) as ws:
                log.info("WebSocket connected")
                async for message in ws:
                    data = json.loads(message)
                    await process_kline(data)

        except Exception as e:
            log.error(f"Scanner error: {e} | Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/scanner.log", encoding="utf-8")
        ]
    )
    asyncio.run(main())
