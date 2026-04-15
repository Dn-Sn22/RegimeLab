<div align="center">

# Binance Trading Bot

### Research-Driven BTC/USDT Trading System with Async Infrastructure, Risk Controls, and ML Roadmap

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Binance](https://img.shields.io/badge/Binance-API-F0B90B?style=flat-square&logo=binance&logoColor=black)](https://binance.com)
[![Claude](https://img.shields.io/badge/Claude-Haiku-CC785C?style=flat-square)](https://anthropic.com)
[![Status](https://img.shields.io/badge/Status-Research%20%26%20Paper%20Trading-yellow?style=flat-square)]()
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)]()

*A Python-based crypto trading research project focused on building robust BTC/USDT signals across changing market regimes.*

</div>

---

This project started as an autonomous BTC/USDT trading bot combining:

- real-time market scanning from Binance
- AI-assisted news and sentiment analysis
- layered risk management
- paper trading execution and monitoring

The current focus is not hype or curve-fitting, but signal quality.

After upgrading the scanner from raw trade ticks to closed 3-minute candles and adding RSI, EMA trend, and volume confirmation filters, I ran a walk-forward backtest to check whether the core Z-score signal had a stable edge.

It did not hold up out-of-sample.

That result changed the direction of the project: the next stage is an ML pipeline built around a labeled Binance market dataset and sequence models such as LSTM.

---

## What This Project Is

This repository is currently best described as:

- an algorithmic trading research project
- a custom Python trading system built without trading frameworks
- a transition from rule-based signals to ML-based signal discovery
- a foundation for a future signal product layer: dashboards, signals, and subscription delivery

## Current Research Findings

Recent work completed:

- migrated the scanner from tick data to closed 3-minute candles
- replaced noisy micro-move detection with candle-based Z-score logic
- added RSI, EMA trend, and volume confirmation filters
- added instant history prefill after reconnect so the scanner does not need to re-warm for 60 minutes
- built a walk-forward backtest on Binance candle history
- created the first labeled dataset for the ML phase
- confirmed that the current Z-score-centered signal does not show stable out-of-sample edge

Main takeaway:

The infrastructure is already solid, but the current core trading signal is not yet robust enough across different market regimes.

That is why the project is now moving toward ML-based signal modeling.

---

## How It Works

Two independent systems must align before the bot opens a position:

```text
[Scanner]   Closed 3-minute candle signal:
            Z-score anomaly
            RSI filter
            EMA trend filter
            volume confirmation

[Research]  AI sentiment confidence > 0.70
            Direction must match scanner signal

         both conditions align

[Risk]    Kelly-based position sizing
          capital protection checks pass

         entry allowed

[Executor]  Paper-trading order execution
[Monitor]   Watches TP / SL / timeout / reverse-signal exit
```

This design filters out weak entries. A raw anomaly alone is not enough: the scanner filters and the research direction both need to agree before risk management allows execution.

---

## Architecture

```text
Binance_trading_bot/
|-- main.py                 # Async orchestrator - runs concurrent tasks
|-- config.py               # All parameters in one place
|-- requirements.txt
|-- .env.example
|-- backtest_wf.py          # Walk-forward validation script
|-- lstm_dataset.py         # Dataset preparation for the ML phase
|-- risk_state.json         # Persisted risk manager state
|
|-- src/
|   |-- scanner.py          # Binance WebSocket scanner on closed 3-minute candles
|   |-- research.py         # News aggregation + Claude sentiment analysis
|   |-- risk.py             # Kelly criterion + capital protection
|   |-- executor.py         # Binance paper-trading execution
|   |-- position_monitor.py # TP / SL / timeout / reverse-signal exit logic
|   `-- telegram_bot.py     # Real-time Telegram notifications
|
`-- logs/
    |-- main.log
    |-- scanner.log
    |-- research.log
    `-- trades.xlsx         # Full trade history with timestamps
```

### Module Details

| Module | Function | Key Technology |
|--------|----------|---------------|
| `scanner.py` | Closed-candle signal engine with Z-score, RSI, EMA, and volume filters | Binance WebSocket API |
| `research.py` | News every 5 minutes, AI sentiment scoring | Claude Haiku, CryptoPanic, RSS, Fear & Greed |
| `risk.py` | Position sizing and capital protections | Kelly Criterion, persistent state |
| `executor.py` | Paper-trading order placement | Binance REST API |
| `position_monitor.py` | TP / SL / timeout / reverse exit checks | asyncio task |
| `telegram_bot.py` | Trade alerts and status updates | Telegram Bot API |

---

## Signal Engine

### Scanner Engine (`scanner.py`)

- streams closed BTC/USDT 3-minute candles from Binance
- computes Z-score of log returns on a rolling window
- computes RSI, EMA trend, and volume confirmation on each closed candle
- generates a bullish or bearish scanner signal only when all filters align
- blocks weak anomalies and logs why they were rejected

### AI Sentiment Engine (`research.py`)

Aggregates from 4 independent sources every 5 minutes:

| Source | Type | Rate Limit Handling |
|--------|------|---------------------|
| CryptoPanic API | News aggregator + community votes | Auto-disable at 600/month limit |
| CoinTelegraph RSS | Crypto media | Unlimited |
| CoinDesk RSS | Crypto media | Unlimited |
| Fear & Greed Index | Market sentiment (0-100) | Unlimited |

All sources are fed to **Claude Haiku** for unified sentiment classification: `bullish / bearish / neutral` with a confidence score. Only signals with `confidence >= 0.70` are passed forward.

---

## Risk Management

Layered capital protection system, state persisted to `risk_state.json`:

| Parameter | Value | Description |
|-----------|-------|-------------|
| Position size | max 5% per trade | Kelly Criterion-adjusted |
| Stop-loss | 8% | Hard floor per position |
| Take-profit | 5% | Fixed exit target |
| Max open positions | 10 (paper) / 3 (live) | Concentration limit |
| Daily loss limit | 10% | Shuts off trading for the day |
| Max drawdown | 25% | Full system halt |
| Entry cooldown | 20 min | Prevents overtrading |
| Min confidence | 0.70 | Signal quality filter |
| Kelly fraction | 0.25 | Conservative fractional Kelly |

**Exit logic** (`position_monitor.py`) checks every 3 seconds:
1. Take-profit hit -> close
2. Stop-loss hit -> close
3. Reverse signal from research -> close early
4. Timeout reached -> close

---

## Tech Stack

```text
Core          Python 3.11, asyncio, Anaconda (botenv)
Exchange      Binance WebSocket API, Binance REST API (testnet)
AI / NLP      Anthropic Claude API (Haiku) for sentiment analysis
Data          CryptoPanic API, CoinTelegraph RSS, CoinDesk RSS
              Alternative.me Fear & Greed Index
Research      Walk-forward backtesting, labeled ML dataset generation
Alerts        Telegram Bot API
Logging       Excel trade log (openpyxl), file logs
Environment   Anaconda, python-dotenv
```

---

## Installation

Requires [Anaconda](https://www.anaconda.com/download).

```bash
git clone https://github.com/Dn-Sn22/Binance_trading_bot.git
cd Binance_trading_bot

conda create -n botenv python=3.11 -y
conda activate botenv
conda install pandas numpy -y
pip install -r requirements.txt

cp .env.example .env
# Fill in your API keys: Binance Testnet, Anthropic, CryptoPanic, Telegram
```

**Required API keys**:

- Binance Testnet API key + secret
- Anthropic API key
- CryptoPanic API token
- Telegram Bot token + chat ID

---

## Usage

```bash
conda activate botenv
cd Binance_trading_bot

# Remove stale state before each session
del risk_state.json   # Windows
# rm risk_state.json  # Linux/Mac

python main.py
```

The bot starts concurrent tasks for scanner, research, and position monitoring. Activity is logged locally and Telegram notifications are sent for important events.

---

## Research Status

- paper-trading infrastructure: working
- scanner redesign to 3-minute candles: completed
- walk-forward validation: completed
- current Z-score core signal: not stable out-of-sample
- ML dataset preparation: completed
- baseline ML models: next
- first LSTM model: next

---

## Roadmap

### Now

- [x] Async trading bot architecture
- [x] Binance scanner on 3-minute candles
- [x] RSI / EMA / volume filters
- [x] reconnect history prefill
- [x] walk-forward backtest
- [x] proof that current core signal lacks stable edge
- [x] prepare ML dataset from Binance candles
- [ ] benchmark baseline models
- [ ] train first LSTM model
- [ ] integrate ML inference into paper trading

### Next

- [ ] market regime detection
- [ ] better execution realism: fees, slippage, latency
- [ ] richer Telegram control layer
- [ ] TUI / dashboard
- [ ] web frontend for signal delivery

### Later

- [ ] multi-asset support
- [ ] multiple strategies
- [ ] product layer and subscription delivery
- [ ] public ML model / dataset release

---

## Performance Targets

The primary benchmark is a stable, risk-aware signal engine rather than a one-off backtest result.

| Metric | Target |
|--------|--------|
| Win rate | > 55% |
| Sharpe Ratio | > 1.5 |
| Max Drawdown | < 15% |
| Monthly return | > 8% |
| Avg R:R ratio | > 1 : 1.5 |

---

## Security

- `TRADING_MODE = testnet` - execution stays on Binance Testnet
- API keys are stored in `.env` and never committed
- `.env.example` is provided with placeholder values only

---

## Contributing

The project is moving toward open collaboration. If you're interested in:

- quantitative strategy development
- ML signal enhancement
- frontend or infrastructure

Feel free to open an issue or reach out directly.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

<div align="center">

*Built with Python, asyncio, and a long-term research mindset.*  
*Currently in research phase - not financial advice.*

</div>
