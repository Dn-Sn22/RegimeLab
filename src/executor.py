import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from binance.client import Client
from binance.exceptions import BinanceAPIException

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from src.risk import RiskDecision, RiskState, load_state, save_state
from src.position_monitor import add_position

log = logging.getLogger(__name__)

DRY_RUN = True


_client = None


class OrderSide(Enum):
    BUY  = "BUY"
    SELL = "SELL"


@dataclass
class OrderResult:
    success:    bool
    order_id:   str
    side:       str
    price:      float
    quantity:   float
    usdt_value: float
    dry_run:    bool
    reason:     str = ""


def get_client() -> Client:
    """Returns the global Binance client."""
    global _client
    if _client is None:
        if config.MODE == "testnet":
            _client = Client(
                config.API_KEY,
                config.API_SECRET,
                testnet=True
            )
        else:
            _client = Client(config.API_KEY, config.API_SECRET)
        log.info(f"Binance client created | Mode: {config.MODE}")
    return _client


def get_current_price(client: Client) -> float:
    """Gets the current BTC/USDT price via REST (fallback only)."""
    try:
        ticker = client.get_symbol_ticker(symbol=config.SYMBOL)
        return float(ticker["price"])
    except Exception as e:
        log.error(f"Error getting price: {e}")
        return 0.0


async def get_price_with_retry(client: Client, retries: int = 3, delay: float = 1.5) -> float:
    """REST fallback with retry — used only if WebSocket price is not passed."""
    for attempt in range(retries):
        price = get_current_price(client)
        if price > 0:
            return price
        if attempt < retries - 1:
            log.warning(f"Price retry {attempt + 1}/{retries} — waiting {delay}s...")
            await asyncio.sleep(delay)
    return 0.0


def get_step_size(client: Client) -> float:
    """Gets the minimum amount step for BTC/USDT."""
    try:
        info = client.get_exchange_info()
        for symbol in info["symbols"]:
            if symbol["symbol"] == config.SYMBOL:
                for f in symbol["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
    except Exception as e:
        log.error(f"LOT_SIZE error: {e}")
    return 0.000001  # fallback


def calculate_quantity(usdt_amount: float, price: float, step_size: float = 0.000001) -> float:
    """Calculates the amount of BTC based on LOT_SIZE."""
    if price == 0:
        return 0.0
    raw      = usdt_amount / price
    quantity = (raw // step_size) * step_size
    return round(quantity, 6)


async def check_balance(client: Client, usdt_amount: float) -> bool:
    """Checks if there is enough USDT on the balance."""
    try:
        balance   = client.get_asset_balance(asset="USDT")
        free_usdt = float(balance["free"])
        required  = usdt_amount * 1.01  # +1% to commission
        if free_usdt < required:
            log.error(
                f"Not enough USDT | "
                f"Need: ${required:.2f} | "
                f"Available: ${free_usdt:.2f}"
            )
            return False
        return True
    except Exception as e:
        log.error(f"Balance check error: {e}")
        return False


async def place_stop_loss(
    client: Client,
    side: OrderSide,
    quantity: float,
    stop_price: float
):
    """Places a stop-loss order after the main one."""
    if DRY_RUN:
        log.info(
            f"[DRY-RUN] STOP-LOSS {side.value} | "
            f"{quantity} BTC @ ${stop_price:,.2f}"
        )
        return

    try:
        limit_price = round(stop_price * 0.99, 2)
        order = client.create_order(
            symbol=config.SYMBOL,
            side=side.value,
            type="STOP_LOSS_LIMIT",
            quantity=quantity,
            price=str(limit_price),
            stopPrice=str(round(stop_price, 2)),
            timeInForce="GTC"
        )
        log.info(f"Stop loss is set | ID: {order['orderId']} | ${stop_price:,.2f}")
    except Exception as e:
        log.error(f"Stop loss error: {e}")


async def place_order(
    client: Client,
    side: OrderSide,
    usdt_amount: float,
    price: float,
    stop_loss: float
) -> OrderResult:
    """Places a limit order + stop loss."""

    step_size = get_step_size(client)
    quantity  = calculate_quantity(usdt_amount, price, step_size)

    if quantity <= 0:
        return OrderResult(
            success=False, order_id="",
            side=side.value, price=price,
            quantity=0.0, usdt_value=0.0,
            dry_run=DRY_RUN,
            reason="Quantity is zero"
        )

    # Dry-run
    if DRY_RUN:
        order_id  = f"DRY-{datetime.utcnow().strftime('%H%M%S')}"
        stop_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        log.info(
            f"[DRY-RUN] {side.value} | "
            f"{quantity} BTC | "
            f"Price: ${price:,.2f} | "
            f"Sum: ${usdt_amount:.2f} | "
            f"Stop: ${stop_loss:,.2f}"
        )
        await place_stop_loss(client, stop_side, quantity, stop_loss)

        return OrderResult(
            success=True, order_id=order_id,
            side=side.value, price=price,
            quantity=quantity, usdt_value=usdt_amount,
            dry_run=True
        )

    # Real order
    try:
        order    = client.create_order(
            symbol=config.SYMBOL,
            side=side.value,
            type="LIMIT",
            timeInForce="GTC",
            quantity=quantity,
            price=str(round(price, 2))
        )
        order_id  = str(order["orderId"])
        stop_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        log.info(
            f"Order placed | ID: {order_id} | "
            f"{side.value} {quantity} BTC @ ${price:,.2f}"
        )

        await place_stop_loss(client, stop_side, quantity, stop_loss)

        return OrderResult(
            success=True, order_id=order_id,
            side=side.value, price=price,
            quantity=quantity, usdt_value=usdt_amount,
            dry_run=False
        )
    except BinanceAPIException as e:
        log.error(f"Binance error: {e}")
        return OrderResult(
            success=False, order_id="",
            side=side.value, price=price,
            quantity=quantity, usdt_value=usdt_amount,
            dry_run=False, reason=str(e)
        )


async def execute_signal(
    signal: str,
    decision: RiskDecision,
    state: RiskState,
    price: float = 0.0,
    z_score: float = 0.0,
    confidence: float = 0.0
) -> OrderResult | None:
    """Receives a signal, checks the balance, places an order."""

    if not decision.allowed:
        log.warning(f"Order cancelled.: {decision.reason}")
        return None

    client = get_client()

    
    if price <= 0:
        log.warning("WebSocket price not provided — falling back to REST with retry")
        price = await get_price_with_retry(client)

    if price == 0:
        log.error("Unable to obtain price - order cancelled")
        return None

    log.info(f"Price used for order: ${price:,.2f}")

    if not DRY_RUN:
        if not await check_balance(client, decision.position_size):
            return None

    side   = OrderSide.BUY if signal == "bullish" else OrderSide.SELL
    result = await place_order(
        client=client,
        side=side,
        usdt_amount=decision.position_size,
        price=price,
        stop_loss=decision.stop_loss
    )

    if result.success:
        state.open_positions += 1
        save_state(state)

        # Save position for exit monitor
        add_position(
            order_id=result.order_id,
            signal=signal,
            price_entry=price,
            position_size=decision.position_size,
            stop_loss=decision.stop_loss,
            z_score=z_score,
            confidence=confidence
        )

        log.info(
            f"Position is open | {side.value} | "
            f"${result.usdt_value:.2f} @ ${result.price:,.2f} | "
            f"TP: +5% | Stop: ${decision.stop_loss:,.2f} | "
            f"ID: {result.order_id}"
        )

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("\n--- Test executor.py ---")

    state    = load_state()
    decision = RiskDecision(
        allowed=True,
        reason="Test",
        position_size=5.0,
        stop_loss=68000.0,
        kelly_pct=5.0
    )

    result = asyncio.run(execute_signal("bullish", decision, state))

    if result:
        print(
            f"Order: {result.side} | "
            f"Quantity: {result.quantity} BTC | "
            f"Sum: ${result.usdt_value:.2f} | "
            f"Dry-run: {result.dry_run}"
        )

    print("\n--- Balance Test ---")
    client = get_client()
    try:
        balance = client.get_asset_balance(asset="USDT")
        print(f"USDT available: ${float(balance['free']):.2f}")
    except Exception as e:
        print(f"Balance Error: {e}")
