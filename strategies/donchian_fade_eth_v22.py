from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeRegime(Strategy):
    METADATA = {
        "name": "DonchianFadeRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.atr_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -100
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
        warmup = max(self.lookback + 2, self.atr_period + 2)
        closes = ctx.closes(warmup)
        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)

        if len(closes) < warmup:
            return None

        # Exit management: time-based exit after max_hold_bars
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_exit_reached", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Cooldown guard: enforce post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate to address prior failure (oos_reject from chopping in strong trends)
        # Only trade during calm or normal environments; avoid volatile/crisis runaway trends
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if ctx.regime in ("volatile", "crisis") or market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.35:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        # Prior lookback window excluding current bar
        prev_highs = highs[-self.lookback - 1 : -1]
        prev_lows = lows[-self.lookback - 1 : -1]
        prior_channel_high = max(prev_highs)
        prior_channel_low = min(prev_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close

        min_penetration = 0.25 * atr

        # Bull Trap / Failed Breakout High -> Go Short
        # Penetrated 20-bar high meaningfully but closed back inside
        if curr_high > prior_channel_high + min_penetration and curr_close < prior_channel_high:
            wick_bps = ((curr_high - curr_close) / curr_close) * 10000.0
            sl_bps = max(140.0, min(300.0, wick_bps + 20.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "failed_breakout_high_fade",
                    "channel_high": prior_channel_high,
                    "bar_high": curr_high,
                    "bar_close": curr_close,
                    "atr": atr,
                    "regime": ctx.regime
                }
            )

        # Bear Trap / Failed Breakdown Low -> Go Long
        # Penetrated 20-bar low meaningfully but closed back inside
        if curr_low < prior_channel_low - min_penetration and curr_close > prior_channel_low:
            wick_bps = ((curr_close - curr_low) / curr_close) * 10000.0
            sl_bps = max(140.0, min(300.0, wick_bps + 20.0))
            tp_bps = sl_bps * 1.5

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "failed_breakdown_low_fade",
                    "channel_low": prior_channel_low,
                    "bar_low": curr_low,
                    "bar_close": curr_close,
                    "atr": atr,
                    "regime": ctx.regime
                }
            )

        return None