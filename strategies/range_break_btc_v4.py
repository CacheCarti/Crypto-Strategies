from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutStrategy(Strategy):
    METADATA = {
        "name": "Failed Breakout BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 24  # Bars to define the range (24 hours for 1h bars)
        self.cooldown_bars = 5  # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.breakout_threshold_factor = 0.001 # 0.1% breakout deviation to avoid noise

        # Indicator periods
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_period = 14

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = self._sma(values[:period], period) # Use SMA for initial EMA
        if ema is None:
            return None
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + self.ema_slow_period + 1)
        highs = ctx.highs(self.range_period + self.ema_slow_period + 1)
        lows = ctx.lows(self.range_period + self.ema_slow_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        # Define the recent range
        recent_high = max(highs[-self.range_period-1:-1]) # Exclude current bar
        recent_low = min(lows[-self.range_period-1:-1]) # Exclude current bar
        
        range_midpoint = (recent_high + recent_low) / 2

        # Regime filters
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Only trade if not in a position
        if ctx.has_position():
            # Exit logic: target range midpoint or opposite edge
            position = ctx.position
            if position["direction"] == "long":
                # If price reaches midpoint or below the lower range (failed long)
                if current_price >= range_midpoint:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_range_midpoint_hit",
                        "current_price": current_price,
                        "range_midpoint": range_midpoint,
                        "recent_high": recent_high,
                        "recent_low": recent_low
                    })
                elif current_price < recent_low * (1 - self.breakout_threshold_factor): # Below range, failed
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_exit_failed_breakout_reversal",
                        "current_price": current_price,
                        "range_midpoint": range_midpoint,
                        "recent_high": recent_high,
                        "recent_low": recent_low
                    })
            elif position["direction"] == "short":
                # If price reaches midpoint or above the upper range (failed short)
                if current_price <= range_midpoint:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_range_midpoint_hit",
                        "current_price": current_price,
                        "range_midpoint": range_midpoint,
                        "recent_high": recent_high,
                        "recent_low": recent_low
                    })
                elif current_price > recent_high * (1 + self.breakout_threshold_factor): # Above range, failed
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_exit_failed_breakout_reversal",
                        "current_price": current_price,
                        "range_midpoint": range_midpoint,
                        "recent_high": recent_high,
                        "recent_low": recent_low
                    })
            return None # Hold position if exit conditions not met

        # Entry logic for failed breakouts
        # Condition 1: Price closed outside the range, then returned inside
        # Condition 2: EMA cross to confirm short-term trend
        # Condition 3: RSI confirmation
        # Condition 4: Market regime filter

        # Long entry: Failed breakdown
        # Price previously broke below, but now closed back inside the range
        prev_close = closes[-2]
        prev_low = lows[-2]
        
        # Look for a bar that closed below the range, and the current bar closed back inside
        if prev_close < recent_low * (1 - self.breakout_threshold_factor) and current_price > recent_low * (1 - self.breakout_threshold_factor) and current_price < recent_high:
            # Confirm with bullish EMA cross and oversold RSI
            if ema_fast > ema_slow and rsi < 50: # RSI below 50 means it's not overbought yet
                # Filter out extreme volatility or crisis
                if regime not in ["CRISIS", "MELTDOWN"] and fear_greed_index > 20: # Avoid extreme fear
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("long", confidence=0.8, metadata={
                        "reason": "failed_breakdown_reversal_long",
                        "current_price": current_price,
                        "prev_close": prev_close,
                        "recent_low": recent_low,
                        "recent_high": recent_high,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "rsi": rsi,
                        "fear_greed_index": fear_greed_index,
                        "regime": regime
                    })

        # Short entry: Failed breakout
        # Price previously broke above, but now closed back inside the range
        prev_high = highs[-2]
        if prev_close > recent_high * (1 + self.breakout_threshold_factor) and current_price < recent_high * (1 + self.breakout_threshold_factor) and current_price > recent_low:
            # Confirm with bearish EMA cross and overbought RSI
            if ema_fast < ema_slow and rsi > 50: # RSI above 50 means it's not oversold yet
                # Filter out extreme volatility or crisis
                if regime not in ["CRISIS", "MELTDOWN"] and fear_greed_index < 80: # Avoid extreme greed
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("short", confidence=0.8, metadata={
                        "reason": "failed_breakout_reversal_short",
                        "current_price": current_price,
                        "prev_close": prev_close,
                        "recent_low": recent_low,
                        "recent_high": recent_high,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "rsi": rsi,
                        "fear_greed_index": fear_greed_index,
                        "regime": regime
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Reset state or log details on position close
        # For simplicity, we just update last_trade_bar in on_bar
        pass