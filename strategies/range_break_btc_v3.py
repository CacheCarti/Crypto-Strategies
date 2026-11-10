from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakout(Strategy):
    METADATA = {
        "name": "Failed Breakout BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour holds
        "warmup_bars": 60, # Need enough bars for range calculation and EMA
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20 # Bars for initial range detection
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _get_range(self, highs, lows, period):
        if len(highs) < period:
            return None, None
        recent_highs = highs[-period:]
        recent_lows = lows[-period:]
        return max(recent_highs), min(recent_lows)

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.range_period + 1)
        highs = ctx.highs(self.range_period + 1)
        lows = ctx.lows(self.range_period + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Get recent range, excluding the very last bar to detect breakouts from previous range
        range_high, range_low = self._get_range(highs[:-1], lows[:-1], self.range_period)

        if range_high is None or range_low is None or range_high == range_low:
            return None

        range_mid = (range_high + range_low) / 2.0

        # Check for failed breakout
        # Condition 1: Price closed above range_high and then came back inside
        if closes[-2] > range_high and current_price < range_high:
            # Short signal: failed breakout to the upside
            if not ctx.has_position():
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    take_profit_bps=(range_high - range_mid) / range_high * 10000, # Target range midpoint
                    stop_loss_bps=(current_price - range_high) / current_price * 10000 + self.METADATA["declared_sl_bps"], # SL above failed breakout high
                    metadata={
                        "reason": "failed_breakout_up",
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_mid": range_mid,
                        "prev_close": closes[-2],
                        "current_close": current_price,
                    }
                )
        # Condition 2: Price closed below range_low and then came back inside
        elif closes[-2] < range_low and current_price > range_low:
            # Long signal: failed breakout to the downside
            if not ctx.has_position():
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    take_profit_bps=(range_mid - range_low) / range_low * 10000, # Target range midpoint
                    stop_loss_bps=(range_low - current_price) / current_price * 10000 + self.METADATA["declared_sl_bps"], # SL below failed breakout low
                    metadata={
                        "reason": "failed_breakout_down",
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_mid": range_mid,
                        "prev_close": closes[-2],
                        "current_close": current_price,
                    }
                )

        # Exit logic if in a position
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]

            # Exit at range midpoint or opposite edge
            if direction == "long":
                if current_price >= range_mid: # Reached midpoint
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "long_tp_range_mid",
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "range_mid": range_mid,
                    })
                elif current_price >= range_high: # Reached opposite edge
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_tp_range_edge",
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "range_high": range_high,
                    })
            elif direction == "short":
                if current_price <= range_mid: # Reached midpoint
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "short_tp_range_mid",
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "range_mid": range_mid,
                    })
                elif current_price <= range_low: # Reached opposite edge
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_tp_range_edge",
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "range_low": range_low,
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy yet, just logging.
        pass