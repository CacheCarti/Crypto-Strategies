from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianTrapFade(Strategy):
    METADATA = {
        "name": "DonchianTrapFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 20
        self.atr_period = 14
        self.min_wick_ratio = 0.35  # Poke beyond channel must be at least 35% of ATR
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 6

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
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period + 10)
        lows = ctx.lows(self.channel_period + 10)

        if len(closes) < self.channel_period + 2:
            return None

        # Time-based exit for existing position
        if ctx.has_position():
            if ctx.bar_index - self.entry_bar >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exit_reached", "bars_held": ctx.bar_index - self.entry_bar}
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Donchian channel evaluated over previous N bars (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        donchian_high = max(prior_highs)
        donchian_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Market regime filter: avoid aggressive counter-trend fading during absolute meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70:
            return None

        min_wick = self.min_wick_ratio * atr

        # Setup 1: Bull trap / failed upside breakout -> Go Short
        # Poked strictly above channel high with significant wick, but closed cleanly back inside
        if curr_high > donchian_high + min_wick and curr_close < donchian_high:
            wick_size = curr_high - donchian_high
            # Dynamic stop loss based on the sweep peak plus safety buffer
            sl_bps = max(140.0, min(300.0, ((curr_high - curr_close) / curr_close) * 10000.0 + 20.0))
            tp_bps = sl_bps * 1.6
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bull_trap_wick_rejection",
                    "donchian_high": donchian_high,
                    "bar_high": curr_high,
                    "wick_size": wick_size,
                    "atr": atr,
                    "close": curr_close
                }
            )

        # Setup 2: Bear trap / failed downside breakdown -> Go Long
        # Poked strictly below channel low with significant wick, but closed cleanly back inside
        if curr_low < donchian_low - min_wick and curr_close > donchian_low:
            wick_size = donchian_low - curr_low
            # Dynamic stop loss based on the sweep trough plus safety buffer
            sl_bps = max(140.0, min(300.0, ((curr_close - curr_low) / curr_close) * 10000.0 + 20.0))
            tp_bps = sl_bps * 1.6
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "bear_trap_wick_rejection",
                    "donchian_low": donchian_low,
                    "bar_low": curr_low,
                    "wick_size": wick_size,
                    "atr": atr,
                    "close": curr_close
                }
            )

        return None