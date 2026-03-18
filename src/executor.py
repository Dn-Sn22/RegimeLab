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

log = logging.getLogger(__name__)

DRY_RUN = True

# Глобальный клиент
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
    """Возвращает глобальный Binance клиент."""
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
        log.info(f"Binance клиент создан | Режим: {config.MODE}")
    return _client

def get_current_price(client: Client) -> float:
    """Получает текущую цену BTC/USDT."""
    try:
        ticker = client.get_symbol_ticker(symbol=config.SYMBOL)
        return float(ticker["price"])
    except Exception as e:
        log.error(f"Ошибка получения цены: {e}")
        return 0.0


def get_step_size(client: Client) -> float:
    """Получает минимальный шаг количества для BTC/USDT."""
    try:
        info = client.get_exchange_info()
        for symbol in info["symbols"]:
            if symbol["symbol"] == config.SYMBOL:
                for f in symbol["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
    except Exception as e:
        log.error(f"LOT_SIZE ошибка: {e}")
    return 0.000001  # fallback


def calculate_quantity(usdt_amount: float, price: float, step_size: float = 0.000001) -> float:
    """Считает количество BTC с учётом LOT_SIZE."""
    if price == 0:
        return 0.0
    raw      = usdt_amount / price
    quantity = (raw // step_size) * step_size
    return round(quantity, 6)


async def check_balance(client: Client, usdt_amount: float) -> bool:
    """Проверяет достаточно ли USDT на балансе."""
    try:
        balance   = client.get_asset_balance(asset="USDT")
        free_usdt = float(balance["free"])
        required  = usdt_amount * 1.01  # +1% на комиссию
        if free_usdt < required:
            log.error(
                f"Недостаточно USDT | "
                f"Нужно: ${required:.2f} | "
                f"Доступно: ${free_usdt:.2f}"
            )
            return False
        return True
    except Exception as e:
        log.error(f"Ошибка проверки баланса: {e}")
        return False


async def place_stop_loss(
    client: Client,
    side: OrderSide,
    quantity: float,
    stop_price: float
):
    """Выставляет стоп-лосс ордер после основного."""
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
        log.info(f"Стоп-лосс выставлен | ID: {order['orderId']} | ${stop_price:,.2f}")
    except Exception as e:
        log.error(f"Стоп-лосс ошибка: {e}")


async def place_order(
    client: Client,
    side: OrderSide,
    usdt_amount: float,
    price: float,
    stop_loss: float
) -> OrderResult:
    """Выставляет лимитный ордер + стоп-лосс."""

    step_size = get_step_size(client)
    quantity  = calculate_quantity(usdt_amount, price, step_size)

    if quantity <= 0:
        return OrderResult(
            success=False, order_id="",
            side=side.value, price=price,
            quantity=0.0, usdt_value=0.0,
            dry_run=DRY_RUN,
            reason="Количество равно нулю"
        )

    # Dry-run
    if DRY_RUN:
        order_id  = f"DRY-{datetime.utcnow().strftime('%H%M%S')}"
        stop_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        log.info(
            f"[DRY-RUN] {side.value} | "
            f"{quantity} BTC | "
            f"Цена: ${price:,.2f} | "
            f"Сумма: ${usdt_amount:.2f} | "
            f"Стоп: ${stop_loss:,.2f}"
        )
        await place_stop_loss(client, stop_side, quantity, stop_loss)

        return OrderResult(
            success=True, order_id=order_id,
            side=side.value, price=price,
            quantity=quantity, usdt_value=usdt_amount,
            dry_run=True
        )

    # Реальный ордер
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
            f"Ордер выставлен | ID: {order_id} | "
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
        log.error(f"Binance ошибка: {e}")
        return OrderResult(
            success=False, order_id="",
            side=side.value, price=price,
            quantity=quantity, usdt_value=usdt_amount,
            dry_run=False, reason=str(e)
        )


async def execute_signal(
    signal: str,
    decision: RiskDecision,
    state: RiskState
) -> OrderResult | None:
    """Принимает сигнал, проверяет баланс, выставляет ордер."""

    if not decision.allowed:
        log.warning(f"Ордер отклонён: {decision.reason}")
        return None

    client = get_client()
    price  = get_current_price(client)

    if price == 0:
        log.error("Не удалось получить цену — ордер отменён")
        return None

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
        log.info(
            f"Позиция открыта | {side.value} | "
            f"${result.usdt_value:.2f} @ ${result.price:,.2f} | "
            f"Стоп: ${decision.stop_loss:,.2f} | "
            f"ID: {result.order_id}"
        )

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("\n--- Тест executor.py ---")

    state    = load_state()
    decision = RiskDecision(
        allowed=True,
        reason="Тест",
        position_size=5.0,
        stop_loss=68000.0,
        kelly_pct=5.0
    )

    result = asyncio.run(execute_signal("bullish", decision, state))

    if result:
        print(
            f"Ордер: {result.side} | "
            f"Кол-во: {result.quantity} BTC | "
            f"Сумма: ${result.usdt_value:.2f} | "
            f"Dry-run: {result.dry_run}"
        )

    print("\n--- Тест баланса ---")
    client = get_client()
    try:
        balance = client.get_asset_balance(asset="USDT")
        print(f"USDT доступно: ${float(balance['free']):.2f}")
    except Exception as e:
        print(f"Ошибка баланса: {e}")
