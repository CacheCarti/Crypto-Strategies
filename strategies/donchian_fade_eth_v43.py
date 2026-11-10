from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "DonchianFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.min_penetration_atr_mult = 0.25
        
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.bars_in_pos = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n = max(self.channel_period + 2, self.atr_period + 2)
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)

        if len(closes) < n:
            return None

        # Manage open position time-stop
        if ctx.has_position():
            self.bars_in_pos += 1
            if self.bars_in_pos >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_time_exit", "bars_held": self.bars_in_pos, "price": ctx.bar.close}
                )
            return None

        self.bars_in_pos = 0

        # Mandatory cooldown after an exit to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out high crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # 20-bar prior channel boundaries (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        min_wick_penetration = atr * self.min_penetration_atr_mult

        # Failed upside breakout: High swept above channel_high by >= min_penetration, but bar closed back inside
        bull_trap = (current_high >= channel_high + min_wick_penetration) and (current_close < channel_high)

        # Failed downside breakout: Low swept below channel_low by >= min_penetration, but bar closed back inside
        bear_trap = (current_low <= channel_low - min_wick_penetration) and (current_close > channel_low)

        if bull_trap and not bear_trap:
            # Dynamic stop calculated from the sweep wick
            wick_distance = current_high - current_close
            sl_bps = max(100.0, min(300.0, (wick_distance / current_close) * 10000.0 * 1.15))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            self.bars_in_pos = 0
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_short_sweep",
                    "channel_high": round(channel_high, 2),
                    "sweep_high": round(current_high, 2),
                    "close": round(current_close, 2),
                    "atr": round(atr, 2),
                    "penetration": round(current_high - channel_high, 2),
                }
            )

        if bear_trap and not bull_trap:
            # Dynamic stop calculated from the sweep wick
            wick_distance = current_close - current_low
            sl_bps = max(100.0, min(300.0, (wick_distance / current_close) * 10000.0 * 1.15))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            self.bars_in_pos = 0
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_long_sweep",
                    "channel_low": round(channel_low, 2),
                    "sweep_low": round(current_low, 2),
                    "close": round(current_close, 2),
                    "atr": round(atr, 2),
                    "penetration": round(channel_low - current_low, 2),
                }
            )

        return None