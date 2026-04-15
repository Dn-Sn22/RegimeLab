import logging
import os

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def telegram_enabled() -> bool:
    """Return True when Telegram credentials are configured."""
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


async def send_message(text: str):
    if not telegram_enabled():
        log.info("Telegram is not configured - skipping notifications")
        return

    try:
        from telegram import Bot

        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
        )
        log.info("Telegram notification sent")
    except Exception as e:
        log.error(f"Telegram error: {e}")


async def notify_signal(
    signal: str,
    price: float,
    stop_loss: float,
    z_score: float,
    confidence: float,
    fear_greed_value: int,
    fear_greed_label: str,
):
    """Trade entry notification."""
    direction = "BULLISH" if signal == "bullish" else "BEARISH"

    text = (
        f"Trade opportunity detected\n\n"
        f"BTC/USDT: {direction}\n"
        f"Entry price: ${price:,.2f}\n"
        f"Stop-loss: ${stop_loss:,.2f}\n"
        f"Z-score: {z_score:+.2f}\n"
        f"Confidence: {confidence}\n"
        f"Fear & Greed: {fear_greed_value} ({fear_greed_label})\n"
    )
    await send_message(text)


async def notify_cryptopanic_disabled():
    """Notify when CryptoPanic becomes unavailable."""
    text = (
        "One of the research sources is temporarily unavailable.\n"
        "The system will continue operating with the remaining sources."
    )
    await send_message(text)


async def notify_startup(mode: str, balance: float):
    """Bot startup notification."""
    text = (
        f"Bot started successfully\n\n"
        f"Mode: {mode.upper()}\n"
        f"Balance: ${balance:.2f}\n\n"
        f"Market monitoring is now active."
    )
    await send_message(text)


async def notify_position_closed(
    signal: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pct: float,
    reason: str,
    order_id: str,
):
    """Position closing notification."""
    direction = "BULLISH" if signal == "bullish" else "BEARISH"

    close_reason = (
        "Take-Profit"
        if "TP" in reason
        else (
            "Stop-Loss"
            if "SL" in reason
            else ("Timeout 12h" if "Time limit" in reason else "Signal Reverse")
        )
    )

    text = (
        f"Position closed\n\n"
        f"Direction: {direction}\n"
        f"Entry: ${entry_price:,.2f}\n"
        f"Exit: ${exit_price:,.2f}\n"
        f"PnL: {pct:+.2f}% (${pnl:+.4f})\n"
        f"Reason: {close_reason}\n"
        f"ID: {order_id}"
    )
    await send_message(text)


async def notify_shutdown(balance: float, open_positions: int, total_trades: int):
    """Notification on bot shutdown."""
    text = (
        "Bot stopped (KeyboardInterrupt).\n\n"
        f"Balance: ${balance:.2f}\n"
        f"Open positions: {open_positions}\n"
        f"Total trades: {total_trades}"
    )
    await send_message(text)
