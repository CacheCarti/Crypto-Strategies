from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPctBReversion(Strategy):
    METADATA = {
        "name": "SolPctBReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.num_std = 2.0
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 3)
        if len(closes) < self.period + 2:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Check regime filter to avoid trading during full meltdown / crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Current and previous Bollinger Bands
        mid_curr, upper_curr, lower_curr = self._bollinger(closes, self.period, self.num_std)
        mid_prev, upper_prev, lower_prev = self._bollinger(closes[:-1], self.period, self.num_std)
        if None in (mid_curr, upper_curr, lower_curr, mid_prev, upper_prev, lower_prev):
            return None

        bandwidth_curr = upper_curr - lower_curr
        bandwidth_prev = upper_prev - lower_prev
        if bandwidth_curr <= 0 or bandwidth_prev <= 0:
            return None

        pct_b_curr = (closes[-1] - lower_curr) / bandwidth_curr
        pct_b_prev = (closes[-2] - lower_prev) / bandwidth_prev

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            direction = ctx.position_direction()

            # Exit at mid-band or timed hold expiration
            if direction == "long":
                if closes[-1] >= mid_curr:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_mid_band_target_reached",
                        "close": closes[-1],
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    })
                if bars_held >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "long_timed_exit",
                        "bars_held": bars_held,
                        "close": closes[-1],
                    })
            elif direction == "short":
                if closes[-1] <= mid_curr:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_mid_band_target_reached",
                        "close": closes[-1],
                        "mid": mid_curr,
                        "pct_b": pct_b_curr,
                    })
                if bars_held >= self.max_hold_bars:
                    return ctx.signal("flat", confidence=0.5, metadata={
                        "reason": "short_timed_exit",
                        "bars_held": bars_held,
                        "close": closes[-1],
                    })
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volatility-adjusted dynamic stops in BPS
        close_price = closes[-1]
        atr_bps = (atr / close_price) * 10000.0
        dynamic_sl = max(180.0, min(420.0, atr_bps * 1.5))
        dynamic_tp = max(220.0, min(500.0, abs(close_price - mid_curr) / close_price * 10000.0))

        # Long: Previous bar was outside lower band (%B < 0), current bar closes back inside (%B >= 0 and %B < 0.35)
        if pct_b_prev < 0.0 and pct_b_curr >= 0.0 and pct_b_curr < 0.35:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "pct_b_failed_breakdown_reversion",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": close_price,
                    "lower_band": lower_curr,
                    "mid_band": mid_curr,
                    "atr_bps": atr_bps,
                }
            )

        # Short: Previous bar was outside upper band (%B > 1), current bar closes back inside (%B <= 1 and %B > 0.65)
        if pct_b_prev > 1.0 and pct_b_curr <= 1.0 and pct_b_curr > 0.65:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dynamic_sl,
                take_profit_bps=dynamic_tp,
                metadata={
                    "reason": "pct_b_failed_breakout_reversion",
                    "pct_b_prev": pct_b_prev,
                    "pct_b_curr": pct_b_curr,
                    "close": close_price,
                    "upper_band": upper_curr,
                    "mid_band": mid_curr,
                    "atr_bps": atr_bps,
                }
            )

        return None