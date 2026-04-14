# Trading Strategy: Market Maker V3

1. Target: Spread capture
Placing limit orders on both sides. Buy at bid, sell at ask. Profit is the spread, repeated many times across thousands of ticks.

2. Price prediction: Micro-Price
Uses Weighted mid = (best_bid * ask_vol + best_ask * bid_vol) / total_vol.
If bid vol >> ask vol, price will likely move up. This predicts the next price shift before it happens.

3. Position management: Skewing
Used to manage inventory risk. If position is long, drop prices to sell faster. If short, raise prices to buy back faster.
Skew = -(pos / limit) * 2.5 factor.
Keeps inventory near flat to avoid being caught in a market trend.

4. Risk: Volatility Clamping
Tracks price speed with a decay score. During fast moves or gaps, the spread widens by up to 4 units automatically.
Prevents getting sniped at bad prices during flash events.

5. Speed
100ms execution. Aggressive quotes hugging the micro-price for max fill rate.
