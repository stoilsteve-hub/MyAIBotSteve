# Binance Spot Trading Bot (ETHEUR / ETHUSDT)

A safety-focused Python bot for spot trading on Binance. Designed to trade a single pair (e.g., `ETHEUR` or `ETHUSDT`) using a rigid "pot" system to limit risk.

**Recent Update (Quote Asset Generalization):**
The bot now dynamically detects the **Quote Asset** (e.g., EUR, USDT, BTC) based on your configured `SYMBOL`.
- `pot_usdt` is now `pot_quote` internally.
- Logs will display the correct currency (e.g., "10.50 EUR" instead of "10.50 USDT").
- Backward compatibility: `.env` variables `TRADE_VALUE_USDT` still work but map to `TRADE_VALUE_QUOTE`.

## ⚠️ CRITICAL SAFETY WARNINGS
**THIS BOT TRADES REAL MONEY.**
1.  **SPOT ONLY**: This bot is designed for Spot trading. Do **not** use it with Futures or Margin.
2.  **DISABLE WITHDRAWALS**: Ensure your API Key has **Withdrawals Disabled**. The bot does not need it.
3.  **NO BORROWING**: The bot never requests margin loans.
4.  **KILL SWITCH**: The bot stops automatically if:
    *   Daily Loss exceeds `MAX_DAILY_LOSS_QUOTE`.
    *   Daily Trade Count exceeds `MAX_TRADES_PER_DAY`.
    *   API Errors exceed `ERROR_LIMIT`.

## Key Features
- **Pot System**: Isolates funds. Starts with ~10 EUR/USDT (configurable).
- **Safety First**:
- **Limit Orders**: Uses LIMIT orders with timeouts. Smartly handles partial fills (only flips state if position is cleared/filled significantly).
- **Trend Filter**: Uses a Simple Moving Average (SMA) to block buying in downtrends.
- **Safety First**:
  - **Real Order Probe**: Places and cancels a real LIMIT order on startup to ensure account permissions.
  - **Fail Fast**: Exits immediately on critical API errors (-2010).
- **No Withdrawals**: Designed for API keys with "Trade Only" permissions.
- **NO BORROWING**: The bot never requests margin loans.
- **KILL SWITCH**: The bot stops automatically if:
    *   Daily Loss exceeds `MAX_DAILY_LOSS_USDT`.
    *   Daily Trade Count exceeds `MAX_TRADES_PER_DAY`.
    *   API Errors exceed `ERROR_LIMIT`.
## Features
*   **Virtual Pot**: The bot isolates a specific amount of USDT (`TRADE_VALUE_USDT`) and ignores the rest of your wallet.
*   **Auto-Funding**: If the pot is empty, it sells enough ETH to fund the pot legally (meeting MIN_NOTIONAL).
*   **Daily Reset**: Counters reset at midnight (Europe/Stockholm).
*   **Realized PnL**: Only fully realized losses count towards the safety stop.
*   **Strict Filters**: Respects Binance `minNotional` and `stepSize` to check for validity before sending orders.

## Operating Modes

### Phase 1 — DRY_RUN Mode (Default)
In this safe mode, the bot simulates orders and does not touch your real balance.
*   `DRY_RUN=1` in .env
*   No real trades are sent to Binance.
*   Simulated fills use current market prices.
*   Safe for verifying strategy logic and budget calculation.

### Phase 2 — Live Trading Mode
**WARNING**: This enables real money trading.
*   Requires `DRY_RUN=0` AND `LIVE_TRADING=YES` in .env.
*   Intended for Spot trading only.
*   **Withdrawals must be disabled** on your API key.
*   **Startup Confirmation**: If the bot needs to sell your existing ETH to fund its initial pot, it will pause and ask you to type "I UNDERSTAND" before proceeding.

## Minimum Order Size (Important)
Binance enforces minimum order value (MIN_NOTIONAL) and quantity step sizes (LOT_SIZE).
If your configured `TRADE_VALUE_USDT` is below Binance’s minimum for ETHUSDT (or rounding makes it too small),
the bot will log a warning and skip trades.

With small balances, you may need `TRADE_VALUE_USDT` closer to 10–12 USDT to avoid rejections.

## Virtual Pot Behavior
The bot manages a virtual sub-balance ("pot") tracked in `bot_state.json`:
- It will only trade up to `TRADE_VALUE_USDT` (plus buffer for Binance minimums).
- It will NOT intentionally use your full wallet balance.
- If you manually trade ETH/USDT while the bot is running, the pot accounting may become inaccurate.

## Setup

### 1. Prerequisites
*   Python 3.11+
*   A Binance Account with some ETH (to fund the initial pot).

### 2. Installation
```bash
# Create virtual env
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration (.env)
Create a `.env` file in this directory:
```env
API_KEY=your_binance_api_key
API_SECRET=your_binance_api_secret

# Trading
TRADE_VALUE_USDT=10.0
BUY_DROP_PCT=0.01          # 1% drop
TAKE_PROFIT_PCT=0.012      # 1.2% gain
STOP_LOSS_PCT=0.02         # 2% loss

# Safety
MAX_DAILY_LOSS_USDT=2.0    # Stop after losing 2 USDT today
MAX_TRADES_PER_DAY=10      # Stop after 10 full trades
ERROR_LIMIT=5              # Stop after 5 errors...
ERROR_WINDOW_SECONDS=600   # ...in 10 minutes
COOLDOWN_SECONDS=120       # Wait 120s after every trade
TIMEZONE=Europe/Stockholm

# Market Safety
MAX_SPREAD_PCT=0.003       # 0.3% max spread to avoid volatile entries
MAX_SLIPPAGE_PCT=0.005     # 0.5% max slippage warning
MIN_NOTIONAL_BUFFER=1.05   # 5% buffer above Binance minimum to ensure acceptance
LOOP_INTERVAL_SECONDS=15   # Check price every 15 seconds

# Execution Safety
DRY_RUN=1
REQUIRE_START_CONFIRM=1
LIVE_TRADING=NO



# Option 2: Limit Orders & Trend Filter
LIMIT_OFFSET_PCT=0.001       # 0.1% price offset for Limit Orders
ORDER_TIMEOUT_SECONDS=60     # Wait 60s for order fill
TREND_WINDOW_SAMPLES=60      # SMA window (60 * 15s = 15 mins)
TREND_MIN_SAMPLES=30         # Samples needed to start trading
MIN_FILL_QUOTE=5.0           # Minimum executed value (e.g. 5 EUR) to flip state (prevents dust state flips)
```

## Running the Bot
```bash
python bot.py
```
*   The first time you run it, it may perform a **Sell** if it needs USDT for its "Pot".
*   It creates a `bot_state.json` file. **Do not delete this** while holding a position, or the bot will lose track of its Entry Price!

## Stopping
*   Press `Ctrl+C` in the terminal.
*   The bot saves state safely before exit.

## File Structure
*   `bot.py`: Main logic.
*   `bot_state.json`: Persistent memory (Position, PnL, etc).
*   `bot_activity.log`: Detailed logs of every check and action.
