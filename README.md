# Binance Spot Trading Bot (ETHEUR / ETHUSDT)

A safety-focused Python trading system for Binance Spot. This repository now contains two versions of the bot, allowing you to choose the execution style that fits your needs.

---

## 🤖 Choose Your Bot

### 1. Refined Bot (`bot_candle.py`) — **RECOMMENDED**
This is the most advanced and stable version. It is designed for longevity and robustness.
*   **Execution**: Operates based on **full candle closes** (e.g., 5m, 15m), making it less susceptible to "fake-out" price noise.
*   **Strategy**: Uses Synchronized Snapshots (Bid/Ask/Mid) for consistent pricing and **Walk-The-Limit** execution to ensure best-price fills without slippage.
*   **Asset Support**: Fully dynamic. Derives `BASE_ASSET` and `QUOTE_ASSET` from your `SYMBOL` automatically.
*   **Key Features**: SMA Reversal signaling, Trailing-Stop Reserve Watcher, Stranded Order Protection, and Auto-Scaling State persistence.

### 2. Original Bot (`bot.py`) — **LEGACY**
The original real-time iteration.
*   **Execution**: Continuous polling and immediate reaction to small price movements.
*   **Simplicity**: Uses standard LIMIT offsets and handles a single `SYMBOL`.
*   **Compatibility**: Remains fully functional with the same `.env`.

---

## ⚠️ CRITICAL SAFETY WARNINGS
**THIS BOT TRADES REAL MONEY.**
1.  **SPOT ONLY**: Designed for Spot trading. Do **not** use with Futures or Margin.
2.  **DISABLE WITHDRAWALS**: Your API Key must have **Withdrawals Disabled**.
3.  **NO BORROWING**: The bot never requests margin loans.
4.  **KILL SWITCHES**:
    *   `MAX_DAILY_LOSS_QUOTE`: Stop after losing a set amount today.
    *   `MAX_TRADES_PER_DAY`: Stop after reaching trade capacity.
    *   `ERROR_LIMIT`: Stop after too many API errors.

---

## Operating Modes

### DRY_RUN Mode (Default)
Safe simulation. No real trades are sent to Binance.
*   Set `DRY_RUN=1` in `.env`.
*   Verify logic, targets, and signal frequency before going live.

### Live Trading Mode
**WARNING**: Enables real money trading.
*   Set `DRY_RUN=0` AND `LIVE_TRADING=YES` in `.env`.
*   **Startup Confirmation**: Bot may pause and ask for confirmation if it needs to fund its virtual pot.

---

## Setup & Installation

### 1. Installation
```bash
# Create and activate virtual env
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration (.env)
Use `.env.example` as a template. The shared configuration works for both bots.

```env
API_KEY=your_key
API_SECRET=your_secret

# Trading Pair (e.g., ETHEUR, BTCUSDT)
SYMBOL=ETHEUR

# Execution Style (Recommended for bot_candle.py)
WALK_ENABLED=YES
LIMIT_OFFSET_PCT=0.0
WALK_OFFSET_START_PCT=0.001
WALK_OFFSET_END_PCT=0.0

# Safety
DRY_RUN=0
LIVE_TRADING=YES
```

### 3. Running
Choose the version you want to run:

```bash
# To run the REFINED bot (Recommended):
./venv/bin/python bot_candle.py

# To run the ORIGINAL bot (Legacy):
./venv/bin/python bot.py
```

---

## File Structure
*   `bot_candle.py`: Refined, candle-driven logic with Walk-The-Limit.
*   `bot.py`: Original real-time logic.
*   `bot_state.json`: Persistent memory (State/PnL). Shared by both (do not run both at once!).
*   `bot_activity.log`: Detailed operation logs.
*   `.env`: Your private secrets and configuration.
