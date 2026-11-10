from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,  # ~4 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.atr_period = 14
        self.cooldown_bars = 4
        self.max_hold_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        n = self.lookback + self.atr_period + 2
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)

        if len(closes) < n:
            return None

        # Time-based exit if held beyond horizon
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_exceeded",
                    "bars_held": bars_held,
                    "close": ctx.bar.close
                })
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid high-crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior window reference (excluding the current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        prior_channel_high = max(prior_highs)
        prior_channel_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        min_penetration = 0.35 * atr

        # Setup 1: Bear Trap / Failed Breakdown -> Long
        bull_wick = prior_channel_low - current_low
        if current_low < prior_channel_low and current_close > prior_channel_low and bull_wick >= min_penetration:
            wick_bps = (bull_wick / current_close) * 10000.0
            sl_bps = max(140.0, min(280.0, wick_bps + 40.0))
            tp_bps = sl_bps * 1.55
            self.entry_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.75, stop_loss_bps=sl_bps, take_profit_bps=tp_bps, metadata={
                "reason": "failed_breakdown_liquidity_sweep",
                "wick_bps": round(wick_bps, 1),
                "prior_low": round(prior_channel_low, 2),
                "close": round(current_close, 2),
                "atr": round(atr, 2)
            })

        # Setup 2: Bull Trap / Failed Breakout -> Short
        bear_wick = current_high - prior_channel_high
        if current_high > prior_channel_high and current_close < prior_channel_high and bear_wick >= min_penetration:
            wick_bps = (bear_wick / current_close) * 10000.0
            sl_bps = max(140.0, min(280.0, wick_bps + 40.0))
            tp_bps = sl_bps * 1.55
            self.entry_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.75, stop_loss_bps=sl_bps, take_profit_bps=tp_bps, metadata={
                "reason": "failed_breakout_liquidity_sweep",
                "wick_bps": round(wick_bps, 1),
                "prior_high": round(prior_channel_high, 2),
                "close": round(current_close, 2),
                "atr": round(atr, 2)
            })

        return None