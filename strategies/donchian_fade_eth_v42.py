from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class DonchianFadeTrap(Strategy):
    METADATA = {
        "name": "DonchianFadeTrap",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 20
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

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
        closes = ctx.closes(self.period + 15)
        highs = ctx.highs(self.period + 15)
        lows = ctx.lows(self.period + 15)

        if len(closes) < self.period + 15:
            return None

        # Time-based exit for swing fade
        if ctx.has_position():
            if ctx.bar_index - self.entry_bar >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_hold_reached", "bars_held": ctx.bar_index - self.entry_bar})
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate based on prior evidence: avoid strong trending/crisis states where breakouts follow through
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if crisis_score > 0.4:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Prior 20-bar channel excluding the current forming bar
        prior_highs = highs[-(self.period + 1):-1]
        prior_lows = lows[-(self.period + 1):-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close

        min_wick = 0.25 * atr

        # Bear Trap / False Breakdown -> Go Long
        # Poked below prior low significantly, closed back above prior low, and market is not confirmed bear trend
        if current_low < (channel_low - min_wick) and current_close > channel_low and trend_regime != "bear":
            wick_depth_bps = ((channel_low - current_low) / channel_low) * 10000.0
            sl_bps = max(100.0, min(wick_depth_bps + 40.0, 220.0))
            tp_bps = sl_bps * 1.5
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakdown_bear_trap",
                    "channel_low": round(channel_low, 2),
                    "wick_low": round(current_low, 2),
                    "close": round(current_close, 2),
                    "atr": round(atr, 2),
                    "trend_regime": trend_regime
                }
            )

        # Bull Trap / False Breakout -> Go Short
        # Poked above prior high significantly, closed back below prior high, and market is not confirmed bull trend
        if current_high > (channel_high + min_wick) and current_close < channel_high and trend_regime != "bull":
            wick_height_bps = ((current_high - channel_high) / channel_high) * 10000.0
            sl_bps = max(100.0, min(wick_height_bps + 40.0, 220.0))
            tp_bps = sl_bps * 1.5
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "failed_breakout_bull_trap",
                    "channel_high": round(channel_high, 2),
                    "wick_high": round(current_high, 2),
                    "close": round(current_close, 2),
                    "atr": round(atr, 2),
                    "trend_regime": trend_regime
                }
            )

        return None