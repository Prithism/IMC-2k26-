# How the Defensive Market Maker Works 📈

This algorithm is designed for the **IMC Prosperity 4** trading competition. It is a "Defensive Market Maker"—meaning it prioritizes **consistency** and **risk management** over high-risk gambling.

---

## 1. The Strategy: "The Middleman"
Instead of guessing if a stock will go up or down, this bot acts like a middleman. 
- It buys from people who are desperate to sell (at a discount).
- It sells to people who are desperate to buy (at a premium).
By constantly buying low and selling high across the "spread," it collects small profits hundreds of times a day.

---

## 2. The Brain: EMA & VWAP
To avoid buying right before a price crash, the bot uses two mathematical tools:
- **VWAP (Volume-weighted Price)**: It doesn't just look at the price; it looks at how much *volume* is at that price. A price "blip" with only 1 unit of volume is ignored.
- **EMA (Exponential Moving Average)**: This is a "smoothing" line that tracks the recent trend. If the market is moving down, the bot's "Fair Value" moves down with it, preventing it from buying "expensive garbage."

---

## 3. The Shields: Downside Protection 🛡️
You requested that the bot not go down more than 500. We built three layers of protection for this:
1. **The Circuit Breaker**: The bot tracks its own P&L. If it loses more than **450**, it pulls the emergency brake and stops trading entirely for the rest of the day.
2. **Inventory Scaling**: As the bot reaches its limits (e.g., owning 250 Tomatoes), it slows down its buying. This prevents it from being "over-leveraged" when the market turns.
3. **Trend Following**: By using the EMA, the bot avoids the "Falling Knife" scenario—it won't buy a crashing asset until the price stabilizes.

---

## 4. The Finish: EOD Flattening 🏁
In the final 10 seconds of the trading day, the bot has one goal: **Get to Zero.**
- It ignores its strategy and aggressively sells anything it still owns.
- It buys back anything it is "short."
- This ensures your leaderboard score is based on **Cash Profit**, not risky unsold inventory.

---

## Summary for Beginners
- **Low Risk**: It prioritizes staying alive over making huge bets.
- **Smart Tracking**: It follows the trend so it doesn't get left behind.
- **Automated Safety**: It has a built-in "off switch" to protect your capital.

---

### Files in this Repository:
- `trader.py`: The clean code to upload to the platform.
- `backtest_runner.py`: Run this on your computer to test the logic.
- `arbitrage_trader_complete.py`: The full standalone version for local coding.
