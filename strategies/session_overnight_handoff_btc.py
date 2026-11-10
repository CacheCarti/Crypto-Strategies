from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCOvernightHandoff(Strategy):
    METADATA = {
        "name": "BTC Overnight Handoff",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 12
        self.slow_ema_period = 26
        self.rsi_period = 14
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Session boundaries (UTC)
        self.overnight_start_hour = 0
        self.overnight_end_hour = 6 # Exiting before this hour starts
        self.next_session_start_hour = 12 # Exit before this hour starts

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Exit logic for existing positions
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit before the next session starts (e.g., US session)
                if current_hour >= self.next_session_start_hour:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "long_position_handoff_exit",
                        "current_hour": current_hour,
                        "entry_bar": ctx.position["entry_bar_index"]
                    })
                # Exit if EMA cross against us or RSI drops significantly
                if fast_ema < slow_ema and rsi < 50:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_position_bearish_crossover_rsi_weakness",
                        "fast_ema": fast_ema, "slow_ema": slow_ema, "rsi": rsi
                    })
            elif ctx.position_direction() == "short":
                # Exit before the next session starts (e.g., US session)
                if current_hour >= self.next_session_start_hour:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "short_position_handoff_exit",
                        "current_hour": current_hour,
                        "entry_bar": ctx.position["entry_bar_index"]
                    })
                # Exit if EMA cross against us or RSI rises significantly
                if fast_ema > slow_ema and rsi > 50:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_position_bullish_crossover_rsi_strength",
                        "fast_ema": fast_ema, "slow_ema": slow_ema, "rsi": rsi
                    })
            return ctx.signal("hold")

        # Entry logic - only during the overnight session (UTC 0-6)
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            fear_greed_index = ctx.features.get("fear_greed_index", 50)
            funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

            # Long entry: Fast EMA crosses above Slow EMA, RSI confirms strength, and not too greedy
            if fast_ema > slow_ema and closes[-1] > closes[-2]: # Price confirmation
                if rsi > 55 and fear_greed_index < 70 and funding_rate < 0.0005: # Avoid extreme greed/high funding
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.75, metadata={
                        "reason": "overnight_long_entry_ema_cross_rsi_strength",
                        "fast_ema": fast_ema, "slow_ema": slow_ema, "rsi": rsi,
                        "fear_greed": fear_greed_index, "funding_rate": funding_rate,
                        "current_hour": current_hour
                    })

            # Short entry: Fast EMA crosses below Slow EMA, RSI confirms weakness, and not too fearful
            elif fast_ema < slow_ema and closes[-1] < closes[-2]: # Price confirmation
                if rsi < 45 and fear_greed_index > 30 and funding_rate > -0.0005: # Avoid extreme fear/low funding
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.75, metadata={
                        "reason": "overnight_short_entry_ema_cross_rsi_weakness",
                        "fast_ema": fast_ema, "slow_ema": slow_ema, "rsi": rsi,
                        "fear_greed": fear_greed_index, "funding_rate": funding_rate,
                        "current_hour": current_hour
                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already update last_trade_bar in on_bar for flat signals.
        # This can be used for adaptive logic if needed, but not for this strategy.
        pass