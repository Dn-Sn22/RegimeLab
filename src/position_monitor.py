import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config
from src.risk import load_state, update_state_after_trade

log = logging.getLogger(__name__)

POSITIONS_FILE  = Path("positions.json")
TAKE_PROFIT_PCT = 0.05   # 5%
STOP_LOSS_PCT   = 0.08   # 8%
TIME_LIMIT_HRS  = 12     # max position lifetime in hours


@dataclass
class Position:
    order_id:    str
    signal:      str        # bullish / bearish
    price_entry: float
    position_size: float    # USDT
    stop_loss:   float
    take_profit: float
    z_score:     float
    confidence:  float
    opened_at:   str        # ISO timestamp


def load_positions() -> List[Position]:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        return [Position(**p) for p in data]
    except Exception as e:
        log.error(f"Error loading positions: {e}")
        return []


def save_positions(positions: List[Position]):
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump([asdict(p) for p in positions], f, indent=2)
    except Exception as e:
        log.error(f"Error saving positions: {e}")


def add_position(
    order_id:      str,
    signal:        str,
    price_entry:   float,
    position_size: float,
    stop_loss:     float,
    z_score:       float,
    confidence:    float
) -> Position:
    """Creates and saves a new position."""
    if signal == "bullish":
        take_profit = round(price_entry * (1 + TAKE_PROFIT_PCT), 2)
    else:
        take_profit = round(price_entry * (1 - TAKE_PROFIT_PCT), 2)

    position = Position(
        order_id=order_id,
        signal=signal,
        price_entry=price_entry,
        position_size=position_size,
        stop_loss=stop_loss,
        take_profit=take_profit,
        z_score=z_score,
        confidence=confidence,
        opened_at=datetime.utcnow().isoformat()
    )

    positions = load_positions()
    positions.append(position)
    save_positions(positions)

    log.info(
        f"Position saved | {signal.upper()} | "
        f"Entry: ${price_entry:,.2f} | "
        f"TP: ${take_profit:,.2f} | "
        f"SL: ${stop_loss:,.2f} | "
        f"ID: {order_id}"
    )
    return position


def calculate_pnl(position: Position, current_price: float) -> float:
    """Calculates PnL in USDT."""
    if position.signal == "bullish":
        pct = (current_price - position.price_entry) / position.price_entry
    else:
        pct = (position.price_entry - current_price) / position.price_entry
    return round(position.position_size * pct, 4)



def should_close(
    position: Position,
    current_price: float,
    current_signal: str
) -> tuple[bool, str]:
    """
    Returns (should_close, reason).
    Reverse signal closes only if position is in profit.
    """
    pnl = calculate_pnl(position, current_price)
    
    
    if position.price_entry <= 0:
        return False, "Invalid entry price"

    # Time-based exit - close after 12 hours regardless of PnL
    try:
        opened_at  = datetime.fromisoformat(position.opened_at).replace(tzinfo=timezone.utc)
        now        = datetime.now(timezone.utc)
        hours_open = (now - opened_at).total_seconds() / 3600
        if hours_open >= TIME_LIMIT_HRS:
            return True, f"Time limit {TIME_LIMIT_HRS}h | PnL: ${pnl:+.4f}"
    except Exception as e:
        log.error(f"Time check error: {e}")

    # Take Profit
    if position.signal == "bullish" and current_price >= position.take_profit:
        return True, f"TP hit | +${pnl:.4f}"

    if position.signal == "bearish" and current_price <= position.take_profit:
        return True, f"TP hit | +${pnl:.4f}"

    # Stop Loss
    if position.signal == "bullish" and current_price <= position.stop_loss:
        return True, f"SL hit | -${abs(pnl):.4f}"

    if position.signal == "bearish" and current_price >= position.stop_loss:
        return True, f"SL hit | -${abs(pnl):.4f}"

    # Reverse signal - only close if in profit
    reverse = (
        (position.signal == "bullish" and current_signal == "bearish") or
        (position.signal == "bearish" and current_signal == "bullish")
    )
    if reverse and pnl > 0:
        return True, f"Reverse signal | +${pnl:.4f}"

    if reverse and pnl <= 0:
        log.info(
            f"Reverse signal ignored | Position in loss ${pnl:.4f} | "
            f"Waiting for TP/SL | ID: {position.order_id}"
        )

    return False, ""



async def monitor_positions(
    get_price_fn,       # callable -> float (current WebSocket price)
    get_signal_fn,      # callable -> str (current trade signal)
    notify_fn=None,     # optional async callable for Telegram
    on_close_fn=None    # optional sync callable for xlsx logging
):
    """
    Main monitoring loop. Call this as an asyncio task.
    get_price_fn and get_signal_fn are lambdas from main.py
    that return the latest WebSocket price and signal.
    """
    log.info("Position monitor started")

    while True:
        try:
            positions = load_positions()

            if not positions:
                await asyncio.sleep(5)
                continue

            current_price  = get_price_fn()
            current_signal = get_signal_fn()

            if current_price <= 0:
                await asyncio.sleep(5)
                continue

            to_keep  = []
            to_close = []

            for pos in positions:
                close, reason = should_close(pos, current_price, current_signal)
                if close:
                    to_close.append((pos, reason))
                else:
                    pnl = calculate_pnl(pos, current_price)
                    pct = (pnl / pos.position_size) * 100
                    log.debug(
                        f"Position open | {pos.signal.upper()} | "
                        f"Entry: ${pos.price_entry:,.2f} | "
                        f"Now: ${current_price:,.2f} | "
                        f"PnL: {pct:+.2f}% | ID: {pos.order_id}"
                    )
                    to_keep.append(pos)

            for pos, reason in to_close:
                pnl = calculate_pnl(pos, current_price)
                pct = (pnl / pos.position_size) * 100

                log.info(
                    f"CLOSING | {pos.signal.upper()} | "
                    f"Entry: ${pos.price_entry:,.2f} -> Now: ${current_price:,.2f} | "
                    f"PnL: {pct:+.2f}% (${pnl:+.4f}) | "
                    f"Reason: {reason} | ID: {pos.order_id}"
                )

                # Update risk state
                state = load_state()
                update_state_after_trade(state, pnl)

                # Log close to xlsx
                if on_close_fn:
                    on_close_fn(
                        order_id=pos.order_id,
                        signal=pos.signal,
                        price_entry=pos.price_entry,
                        price_exit=current_price,
                        position_size=pos.position_size,
                        pnl=pnl,
                        pnl_pct=pct,
                        reason=reason
                    )

                # Telegram notification
                if notify_fn:
                    await notify_fn(
                        signal=pos.signal,
                        entry_price=pos.price_entry,
                        exit_price=current_price,
                        pnl=pnl,
                        pct=pct,
                        reason=reason,
                        order_id=pos.order_id
                    )

            if to_close:
                save_positions(to_keep)
                log.info(f"Closed {len(to_close)} position(s) | Remaining: {len(to_keep)}")

        except Exception as e:
            log.error(f"Monitor error: {e}")

        await asyncio.sleep(3)  # check every 3 seconds
