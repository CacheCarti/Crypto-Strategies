from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "DonchianFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,  # 4 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.min_wick_atr_mult = 0.35
        self.max_hold_bars = 5
        self.cooldown_bars = 4
        
        self.last_exit_bar = -999
        self.entry_bar = 0

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
        n = self.channel_period + 5
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)

        if len(closes) < n:
            return None

        # Check existing position management: timed exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                direction = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "timed_exit_max_hold_reached",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid entering against outright crisis / meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Donchian channel established over the PREVIOUS channel_period bars (excluding the current trigger bar)
        prev_highs = highs[- (self.channel_period + 1):-1]
        prev_lows = lows[- (self.channel_period + 1):-1]

        donchian_high = max(prev_highs)
        donchian_low = min(prev_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        min_wick = atr * self.min_wick_atr_mult

        # Failed Bullish Breakout (Bear Trap -> Short)
        # Price spiked above Donchian high by at least min_wick, but closed back below it
        wick_above = curr_high - donchian_high
        if wick_above >= min_wick and curr_close < donchian_high:
            # Dynamic structural stop placed slightly above the hunt wick
            sl_distance = max(curr_high - curr_close, atr * 0.75)
            sl_bps = min(max((sl_distance / curr_close) * 10000.0, 100.0), 300.0)
            tp_bps = min(max(sl_bps * 1.6, 180.0), 500.0)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_short_wick_rejection",
                    "wick_above": round(wick_above, 2),
                    "donchian_high": round(donchian_high, 2),
                    "atr": round(atr, 2),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                }
            )

        # Failed Bearish Breakout (Bull Trap / Stop Hunt -> Long)
        # Price flushed below Donchian low by at least min_wick, but closed back above it
        wick_below = donchian_low - curr_low
        if wick_below >= min_wick and curr_close > donchian_low:
            # Dynamic structural stop placed slightly below the hunt wick
            sl_distance = max(curr_close - curr_low, atr * 0.75)
            sl_bps = min(max((sl_distance / curr_close) * 10000.0, 100.0), 300.0)
            tp_bps = min(max(sl_bps * 1.6, 180.0), 500.0)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "failed_breakout_long_wick_rejection",
                    "wick_below": round(wick_below, 2),
                    "donchian_low": round(donchian_low, 2),
                    "atr": round(atr, 2),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                }
            )

        return None