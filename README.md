# BTC Trading Bot

AI-powered trading bot for BTC/USDT on Binance.
Built as a long-term project to learn algorithmic trading.
Currently running in paper trading mode on Binance Testnet.

## Idea

A swing trading bot that monitors BTC/USDT 24/7.
The goal is to predict market corrections — sell before the drop,
buy back at the bottom, and capture the rebound in profit.

## How It Works

Two conditions must occur simultaneously for the bot to enter a trade:

1. **Scanner** detects a price anomaly (Z-score > 2.0)
2. **Research** receives a strong news signal (confidence > 0.70)

Only then **Risk** calculates position size and **Executor** places the order.

## Architecture
```
scanner.py       real-time BTC price via WebSocket
                 anomaly detection via Z-score of log returns

research.py      news every 5 minutes
                 CryptoPanic (auto-disable on rate limit) +
                 CoinTelegraph RSS + CoinDesk RSS +
                 Fear & Greed Index
                 sentiment analysis via Claude API (bullish/bearish/neutral)

risk.py          6 levels of capital protection
                 Kelly Criterion for position sizing
                 state saved to risk_state.json
                 auto-reset positions on bot restart

executor.py      limit order + stop-loss on Binance
                 dry-run mode, no real money spent

main.py          orchestrator for all modules
                 three async tasks running in parallel
                 logs every trade to logs/trades.xlsx
```

## Risk Parameters

| Parameter | Value |
|-----------|-------|
| Starting balance | $100 |
| Position size | max 5% of balance |
| Stop-loss | 8% |
| Max positions | 10 (paper trading) / 3 (live) |
| Daily loss limit | 10% |
| Max drawdown | 25% |
| Entry cooldown | 30 sec |
| Min signal confidence | 0.70 |

## News Sources

| Source | Type | Limit |
|--------|------|-------|
| CryptoPanic | Aggregator + community votes | 600/month, auto-disables on limit |
| CoinTelegraph RSS | Media | Unlimited |
| CoinDesk RSS | Media | Unlimited |
| Fear & Greed Index | Market sentiment | Unlimited |

## Installation
Requires [Anaconda](https://www.anaconda.com/download) — used for environment management.
```bash
git clone https://github.com/Dn-Sn22/Binance_trading_bot.git

conda create -n botenv python=3.11 -y
conda activate botenv
conda install pandas numpy -y
pip install -r requirements.txt

cp .env.example .env
# Fill .env file with your API keys
```


## Usage
```bash
conda activate botenv
python main.py
```



## Project Structure
```
Binance_trading_bot/
├── main.py              # orchestrator
├── config.py            # settings
├── requirements.txt
├── .env.example
├── src/
│   ├── scanner.py       # WebSocket + Z-score
│   ├── research.py      # news + Claude sentiment
│   ├── risk.py          # Kelly + capital protection
│   └── executor.py      # Binance orders
└── logs/
    ├── main.log
    ├── scanner.log
    ├── research.log
    └── trades.xlsx      # trade history
```

## Module Status

| Module | Status |
|--------|--------|
| scanner.py | Done |
| research.py | Done |
| risk.py | Done |
| executor.py | Done |
| main.py | Done |
| Exit logic | In progress |
| Dashboard | In progress |
| Backtesting | Planned |

## Tech Stack

- Python 3.11 + asyncio
- Binance WebSocket API
- Claude API (Anthropic) — sentiment analysis
- CryptoPanic API — crypto news aggregator
- CoinTelegraph + CoinDesk RSS — crypto media
- Fear & Greed Index — market sentiment
- Kelly Criterion — capital management
- Z-score of log returns — anomaly detection
- Anaconda — environment management

## Security

- `DRY_RUN = True` — no real orders placed
- Everything tested on Testnet first
