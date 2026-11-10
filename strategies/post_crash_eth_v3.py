from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostPanicDrift(Strategy):
    METADATA = {
        "name": "Post-Panic Drift Strategy ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.drop_bars = 5
        self.drop_threshold_pct = -1.5 # % drop over drop_bars
        self.consolidation_bars = 3
        self.consolidation_range_pct = 0.5 # price range % during consolidation
        self.cooldown_period = 12 # bars to wait after a trade
        self.last_trade_bar = -self.cooldown_period
        self.panic_detected = False
        self.panic_end_bar = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_period:
            return None

        current_price = closes[-1]
        
        # Calculate EMAs
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        # Panic detection: sharp drop over `drop_bars`
        if not self.panic_detected:
            if len(closes) >= self.drop_bars:
                price_change = ((current_price - closes[-self.drop_bars]) / closes[-self.drop_bars]) * 100
                if price_change <= self.drop_threshold_pct:
                    self.panic_detected = True
                    self.panic_end_bar = ctx.bar_index
                    # print(f"Panic detected at bar {ctx.bar_index}, price change: {price_change:.2f}%")
                    return None # Do not trade immediately after panic

        # If panic was detected, check for consolidation
        if self.panic_detected:
            if ctx.bar_index >= self.panic_end_bar + self.consolidation_bars:
                # Check consolidation over the last `consolidation_bars`
                consolidation_closes = closes[-self.consolidation_bars:]
                consolidation_highs = highs[-self.consolidation_bars:]
                consolidation_lows = lows[-self.consolidation_bars:]

                if len(consolidation_closes) == self.consolidation_bars:
                    max_price = max(consolidation_highs)
                    min_price = min(consolidation_lows)
                    price_range_pct = ((max_price - min_price) / min_price) * 100

                    # Entry condition: low price range after panic, and fast EMA crossing above slow EMA
                    if price_range_pct <= self.consolidation_range_pct and fast_ema > slow_ema and closes[-2] <= slow_ema and current_price > slow_ema:
                        if not ctx.has_position():
                            self.last_trade_bar = ctx.bar_index
                            self.panic_detected = False # Reset panic state after entry
                            confidence = 0.6 + (1 - ctx.features.get("fear_greed_index", 50) / 100) * 0.3 # Higher confidence in fear
                            confidence = min(1.0, confidence)
                            
                            eth_24h_return = ctx.features.get("eth_return_pct", 0.0)
                            
                            return ctx.signal(
                                "long",
                                confidence=confidence,
                                metadata={
                                    "reason": "post_panic_drift_long",
                                    "panic_start_bar": self.panic_end_bar - self.drop_bars + 1,
                                    "panic_end_bar": self.panic_end_bar,
                                    "consolidation_bars": self.consolidation_bars,
                                    "price_range_pct": round(price_range_pct, 2),
                                    "fast_ema": round(fast_ema, 2),
                                    "slow_ema": round(slow_ema, 2),
                                    "current_price": round(current_price, 2),
                                    "fear_greed_index": ctx.features.get("fear_greed_index", 50),
                                    "eth_24h_return_pct": round(eth_24h_return, 2)
                                }
                            )
            elif ctx.bar_index > self.panic_end_bar + self.cooldown_period * 2: # If too much time passes, reset panic
                self.panic_detected = False
                # print(f"Panic state reset due to timeout at bar {ctx.bar_index}")

        # Exit logic
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # For simplicity, exit is handled by SL/TP and horizon_seconds
                # However, we can add a condition to exit if price drops below slow EMA after a drift
                if current_price < slow_ema and fast_ema < slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "drift_reversal_exit",
                            "fast_ema": round(fast_ema, 2),
                            "slow_ema": round(slow_ema, 2),
                            "current_price": round(current_price, 2),
                            "fear_greed_index": ctx.features.get("fear_greed_index", 50)
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset panic state regardless of how position closed
        self.panic_detected = False
        # print(f"Position closed at bar {ctx.bar_index}. Return: {position['return_bps']:.2f} bps. Bars held: {position['bars_held']}")
        # We can add adaptive logic here, e.g., adjust cooldown or confidence based on recent performance
        pass