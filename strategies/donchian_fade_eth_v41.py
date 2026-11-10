from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.entry_bar_idx = -100
        self.last_exit_idx = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_idx = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.lookback + 15
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # Handle active position management (time-based exit)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_exit_reached",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_idx) < self.cooldown_bars:
            return None

        # Donchian channel over previous lookback bars (excluding current bar)
        prior_highs = highs[-self.lookback - 1 : -1]
        prior_lows = lows[-self.lookback - 1 : -1]
        dh_prev = max(prior_highs)
        dl_prev = min(prior_lows)

        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val <= 0:
            return None

        min_wick_extension = 0.25 * atr_val

        curr_bar = ctx.bar
        curr_high = curr_bar.high
        curr_low = curr_bar.low
        curr_close = curr_bar.close

        # Avoid entries in extreme crisis volatility
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        # Bear trap setup: Low poked below range by min_wick, but closed safely back inside
        if (curr_low < dl_prev - min_wick_extension) and (curr_close > dl_prev):
            # Dynamic SL based on the wick penetration depth + buffer
            wick_penetration_bps = ((dl_prev - curr_low) / curr_close) * 10000.0
            sl_bps = max(150.0, min(350.0, wick_penetration_bps + 40.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bear_trap_failed_breakdown_fade",
                    "donchian_low": dl_prev,
                    "bar_low": curr_low,
                    "bar_close": curr_close,
                    "atr": atr_val,
                    "wick_penetration_bps": wick_penetration_bps,
                }
            )

        # Bull trap setup: High poked above range by min_wick, but closed back inside
        if (curr_high > dh_prev + min_wick_extension) and (curr_close < dh_prev):
            wick_penetration_bps = ((curr_high - dh_prev) / curr_close) * 10000.0
            sl_bps = max(150.0, min(350.0, wick_penetration_bps + 40.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bull_trap_failed_breakout_fade",
                    "donchian_high": dh_prev,
                    "bar_high": curr_high,
                    "bar_close": curr_close,
                    "atr": atr_val,
                    "wick_penetration_bps": wick_penetration_bps,
                }
            )

        return None