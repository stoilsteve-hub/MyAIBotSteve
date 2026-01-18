import os
from dotenv import load_dotenv

# Load .env file
loaded = load_dotenv()
print(f"Loaded .env file: {'Yes' if loaded else 'No'}")

# Secrets (Boolean check only)
print(f"API_KEY present: {'yes' if os.getenv('API_KEY') else 'no'}")
print(f"API_SECRET present: {'yes' if os.getenv('API_SECRET') else 'no'}")

# Configuration Keys to Check
keys = [
    "TRADE_VALUE_USDT", "BUY_DROP_PCT", "TAKE_PROFIT_PCT", "STOP_LOSS_PCT",
    "MAX_DAILY_LOSS_USDT", "MAX_TRADES_PER_DAY", "LOOP_INTERVAL_SECONDS", 
    "COOLDOWN_SECONDS", "ERROR_LIMIT", "DRY_RUN", "REQUIRE_START_CONFIRM",
    "ERROR_WINDOW_SECONDS", "MAX_SPREAD_PCT", "MAX_SLIPPAGE_PCT", 
    "MIN_NOTIONAL_BUFFER", "TIMEZONE", "LIVE_TRADING"
]

print("\n--- Configuration Variables ---")
found_count = 0
for k in keys:
    val = os.getenv(k)
    status = "found" if val is not None else "MISSING (using default)"
    print(f"{k}: {status}")
    if val is not None: found_count += 1

print(f"\nTotal Configs Found: {found_count}/{len(keys)}")
