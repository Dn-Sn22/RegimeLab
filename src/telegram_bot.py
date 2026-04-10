import logging
import os
import asyncio

log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Graceful degradation — If the token is not set, just remain silent
def telegram_enabled() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


async def send_message(text: str):
    if not telegram_enabled():
        log.info("Telegram isn't configured — skipping notifications")
        return

    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML"
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
    fear_greed_label: str
):
    """Trade Entry Notification."""
    direction = "BULLISH" if signal == "bullish" else "BEARISH"

    text = (
        f"Sir, your servant has discovered a favorable opportunity..\n\n"
        
        f"📊 BTC/USDT — {direction}\n"
        f"Entry price: ${price:,.2f}\n"
        f"SL: ${stop_loss:,.2f}\n"
        f"Z-score: {z_score:+.2f}\n"
        f"Confidence: {confidence}\n"
        f"Fear & Greed: {fear_greed_value} ({fear_greed_label})\n\n"
    )
    await send_message(text)


async def notify_cryptopanic_disabled():
    """CryptoPanic Disconnection Notification."""
    text = (
        f"Sir, one of our intelligence sources is temporarily unavailable..\n"
        f"Nazarick continues the operation with its remaining resources.."
    )
    await send_message(text)


async def notify_startup(mode: str, balance: float):
    """Уведомление о запуске бота."""
    text = (
        f"🔱 Nazarick has awakened and is ready to serve its master..\n\n"
        f" Mode: {mode.upper()}\n"
        f" Balance: ${balance:.2f}\n\n"
        f"Market monitoring has begun. Your servant is vigilant.."
    )
    await send_message(text)
    
    
async def notify_position_closed(
    signal: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pct: float,
    reason: str,
    order_id: str
):
   
   
    """Position Closing Notification."""
    direction = "BULLISH" if signal == "bullish" else "BEARISH"
    
    text = (
        f"Position is closed\n\n"
        f" {direction}\n"
        f"Entry: ${entry_price:,.2f}\n"
        f"Exit: ${exit_price:,.2f}\n"
        f"PnL: {pct:+.2f}% (${pnl:+.4f})\n"
        f" Reason: {'Take-Profit' if 'TP' in reason else ('Stop-Loss' if 'SL' in reason else ('Timeout 12h' if 'Time limit' in reason else 'signal Reverse'))}\n"
        f"ID: {order_id}\n\n"
        f"As your wisdom has provided, the position is closed.."
    )
    await send_message(text)

async def notify_shutdown(balance: float, open_positions: int, total_trades: int):
    """Notification on bot shutdown (Ctrl+C)."""
    text = (
        f"Stopping the bot (KeyboardInterrupt).\n\n"
        f"Balance: ${balance:.2f}\n"
        f"Open positions: {open_positions}\n"
        f"Total trades: {total_trades}"
    )
    await send_message(text)
