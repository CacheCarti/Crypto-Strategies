from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeHunter(Strategy):
    METADATA = {
        "name": "Donchian Fade Hunter",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = max(self.channel_period, self.atr_period) + 2
        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)

        if len(closes) < warmup:
            return None

        # Position management: time-based exit to prevent bleeding
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exit", "bars_held": bars_held, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate ATR for dynamic thresholding
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar high/low (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        # Require a meaningful probe outside the boundary: at least 0.25 ATR stop run
        min_penetration = 0.25 * atr

        # Avoid entries in extreme crisis volatility
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Setup 1: Bear Trap / Failed Breakdown -> Go Long
        # Price swept below range_low by meaningful margin, but closed back inside
        if current_low < (range_low - min_penetration) and current_close > range_low:
            # Stop loss anchored slightly below the sweep low in bps
            sl_distance = max(current_close - current_low + (0.1 * atr), 0.008 * current_close)
            sl_bps = min(max((sl_distance / current_close) * 10000.0, 140.0), 280.0)
            tp_bps = sl_bps * 1.6  # Positive risk-reward ratio

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakdown_fade",
                    "range_low": range_low,
                    "sweep_low": current_low,
                    "penetration": range_low - current_low,
                    "atr": atr,
                    "close": current_close
                }
            )

        # Setup 2: Bull Trap / Failed Breakout -> Go Short
        # Price swept above range_high by meaningful margin, but closed back inside
        if current_high > (range_high + min_penetration) and current_close < range_high:
            sl_distance = max(current_high - current_close + (0.1 * atr), 0.008 * current_close)
            sl_bps = min(max((sl_distance / current_close) * 10000.0, 140.0), 280.0)
            tp_bps = sl_bps * 1.6

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_fade",
                    "range_high": range_high,
                    "sweep_high": current_high,
                    "penetration": current_high - range_high,
                    "atr": atr,
                    "close": current_close
                }
            )

        return None