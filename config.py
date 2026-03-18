import os
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("TRADING_MODE", "testnet")

if MODE == "live":
    API_KEY    = os.getenv("BINANCE_API_KEY")
    API_SECRET = os.getenv("BINANCE_SECRET_KEY")
    BASE_URL   = "https://api.binance.com"
    WS_URL     = "wss://stream.binance.com:9443"
else:
    API_KEY    = os.getenv("BINANCE_TESTNET_API_KEY")
    API_SECRET = os.getenv("BINANCE_TESTNET_SECRET_KEY")
    BASE_URL   = "https://testnet.binance.vision"
    WS_URL     = "wss://testnet.binance.vision/ws"

SYMBOL           = "BTCUSDT"
INTERVAL         = "1m"
MAX_POSITION_PCT = 0.05
DAILY_LOSS_LIMIT = 0.10

print(f"[config] Mode: {MODE} | Symbol: {SYMBOL} | Base: {BASE_URL}")

# Kelly параметры
WIN_RATE = 0.55
AVG_WIN  = 0.06
AVG_LOSS = 0.03