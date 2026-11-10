from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFailedBreakoutFade(Strategy):
    METADATA = {
        "name": "EthFailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 330.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.atr_period = 14
        self.min_wick_atr_mult = 0.35
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period):
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
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)

        if len(closes) < self.lookback + self.atr_period:
            return None

        current_idx = ctx.bar_index

        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            if bars_held >= self.max_hold_bars:
                direction = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_based_fade_exit", "bars_held": bars_held, "direction": direction}
                )
            return None

        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes where fades perform poorly
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        # Calculate prior channel excluding the current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        if not prior_highs or not prior_lows:
            return None

        prev_channel_high = max(prior_highs)
        prev_channel_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        min_wick = atr * self.min_wick_atr_mult

        # Bear trap (False breakdown): price pierced below support, but closed back above it
        if current_low < (prev_channel_low - min_wick) and current_close > prev_channel_low:
            wick_depth_bps = ((prev_channel_low - current_low) / current_close) * 10000.0
            self.entry_bar = current_idx
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_fade_reversal",
                    "channel_low": prev_channel_low,
                    "bar_low": current_low,
                    "bar_close": current_close,
                    "wick_depth_bps": wick_depth_bps,
                    "atr": atr
                }
            )

        # Bull trap (False breakout): price pierced above resistance, but closed back below it
        if current_high > (prev_channel_high + min_wick) and current_close < prev_channel_high:
            wick_depth_bps = ((current_high - prev_channel_high) / current_close) * 10000.0
            self.entry_bar = current_idx
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_fade_reversal",
                    "channel_high": prev_channel_high,
                    "bar_high": current_high,
                    "bar_close": current_close,
                    "wick_depth_bps": wick_depth_bps,
                    "atr": atr
                }
            )

        return None