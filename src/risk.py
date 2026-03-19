import logging
import json
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

log = logging.getLogger(__name__)

# Параметры риска из config
KELLY_FRACTION   = 0.25
MAX_POSITION_PCT = 0.05
MAX_POSITIONS    = 10
MIN_CONFIDENCE   = 0.70
DAILY_LOSS_LIMIT = 0.10
MAX_DRAWDOWN     = 0.25
STOP_LOSS_PCT    = 0.08

STATE_FILE = Path("risk_state.json")


@dataclass
class RiskState:
    balance:        float
    peak_balance:   float
    daily_start:    float
    daily_date:     str
    open_positions: int   = 0
    daily_loss:     float = 0.0
    total_trades:   int   = 0
    blocked:        bool  = False


@dataclass
class RiskDecision:
    allowed:       bool
    reason:        str
    position_size: float
    stop_loss:     float
    kelly_pct:     float


def load_state() -> RiskState:
    """Загружает состояние из файла или создаёт новое."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            log.info(f"Состояние загружено | Баланс: ${data['balance']:.2f}")
            return RiskState(**data)
        except Exception as e:
            log.error(f"Ошибка загрузки состояния: {e} — создаём новое")

    log.info("Новое состояние | Баланс: $100.00")
    return RiskState(
        balance=100.0,
        peak_balance=100.0,
        daily_start=100.0,
        daily_date=date.today().isoformat()
    )


def save_state(state: RiskState):
    """Сохраняет состояние в файл."""
    try:
        data = asdict(state)
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error(f"Ошибка сохранения состояния: {e}")


def unblock_bot(state: RiskState) -> bool:
    """Разблокировать бота вручную если восстановили 50% просадки."""
    if state.blocked:
        peak = state.peak_balance
        if state.balance > peak * 0.5:
            state.blocked = False
            log.info("Бот разблокирован")
            save_state(state)
            return True
        else:
            log.warning(
                f"Разблокировка невозможна | "
                f"Баланс ${state.balance:.2f} < 50% пика ${peak * 0.5:.2f}"
            )
            return False
    return True


def kelly_position_size(
    balance: float,
    win_rate: float,
    avg_win: float,
    avg_loss: float
) -> float:
    """Считает размер позиции по формуле Kelly."""
    if avg_loss == 0:
        return 0.0

    p = win_rate
    q = 1 - win_rate
    b = avg_win / avg_loss

    kelly_full     = max(0.0, (p * b - q) / b)
    kelly_fraction = min(kelly_full * KELLY_FRACTION, MAX_POSITION_PCT)
    position_usd   = balance * kelly_fraction

    return round(position_usd, 2)


def check_risk(
    state: RiskState,
    signal: str,
    confidence: float,
    current_price: float,
    win_rate: float = config.WIN_RATE,
    avg_win: float  = config.AVG_WIN,
    avg_loss: float = config.AVG_LOSS
) -> RiskDecision:
    """Главная функция проверки риска."""

    # Обновляем дневные лимиты если новый день
    today = date.today().isoformat()
    if state.daily_date != today:
        state.daily_date  = today
        state.daily_start = state.balance
        state.daily_loss  = 0.0
        log.info(f"Новый день — сброс лимитов | Баланс: ${state.balance:.2f}")

    def deny(reason: str) -> RiskDecision:
        return RiskDecision(
            allowed=False, reason=reason,
            position_size=0.0, stop_loss=0.0, kelly_pct=0.0
        )

    if state.blocked:
        return deny("Бот заблокирован")

    if signal == "neutral":
        return deny("Сигнал neutral — нет входа")

    if confidence < MIN_CONFIDENCE:
        return deny(f"Уверенность {confidence:.2f} ниже порога {MIN_CONFIDENCE}")

    if state.open_positions >= MAX_POSITIONS:
        return deny(f"Открыто {state.open_positions} позиций — максимум")

    daily_loss_pct = state.daily_loss / state.daily_start if state.daily_start > 0 else 0
    if daily_loss_pct >= DAILY_LOSS_LIMIT:
        return deny(f"Дневной лимит {daily_loss_pct*100:.1f}% — стоп")

    drawdown = (state.peak_balance - state.balance) / state.peak_balance
    if drawdown >= MAX_DRAWDOWN:
        state.blocked = True
        save_state(state)
        return deny(f"Просадка {drawdown*100:.1f}% — бот заблокирован")

    position_size = kelly_position_size(state.balance, win_rate, avg_win, avg_loss)
    if position_size < 1.0:
        return deny(f"Размер позиции ${position_size:.2f} слишком мал")

    stop_loss_price = (
        current_price * (1 - STOP_LOSS_PCT) if signal == "bullish"
        else current_price * (1 + STOP_LOSS_PCT)
    )
    kelly_pct = (position_size / state.balance) * 100

    log.info(
        f"Риск OK | {signal} | "
        f"Размер: ${position_size:.2f} ({kelly_pct:.1f}%) | "
        f"Стоп: ${stop_loss_price:,.2f}"
    )

    return RiskDecision(
        allowed=True,
        reason="Все проверки пройдены",
        position_size=position_size,
        stop_loss=round(stop_loss_price, 2),
        kelly_pct=round(kelly_pct, 2)
    )


def update_state_after_trade(state: RiskState, pnl: float) -> RiskState:
    """Обновляет состояние после закрытия сделки."""
    state.balance        += pnl
    state.total_trades   += 1
    state.open_positions  = max(0, state.open_positions - 1)

    if pnl < 0:
        state.daily_loss += abs(pnl)

    if state.balance > state.peak_balance:
        state.peak_balance = state.balance

    save_state(state)

    log.info(
        f"Сделка закрыта | PnL: {pnl:+.2f}$ | "
        f"Баланс: ${state.balance:.2f} | "
        f"Просадка: {((state.peak_balance - state.balance) / state.peak_balance)*100:.1f}%"
    )
    return state


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    state = load_state()

    print("\n--- Тест risk.py ---")

    decision = check_risk(state, "bullish", 0.85, 74000.0)
    print(f"Тест 1 (bullish 0.85): {decision.allowed} | {decision.reason} | ${decision.position_size}")

    decision = check_risk(state, "bullish", 0.50, 74000.0)
    print(f"Тест 2 (conf 0.50):    {decision.allowed} | {decision.reason}")

    decision = check_risk(state, "neutral", 0.90, 74000.0)
    print(f"Тест 3 (neutral):      {decision.allowed} | {decision.reason}")

    state.daily_loss = 11.0
    decision = check_risk(state, "bullish", 0.85, 74000.0)
    print(f"Тест 4 (daily loss):   {decision.allowed} | {decision.reason}")
