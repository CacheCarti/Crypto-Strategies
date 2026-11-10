from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.max_width_pct = 1.35
        self.min_width_pct = 0.45

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
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.channel_period + 5:
            return None

        # Cooldown guard to tightly control trade count over 17,000 bars
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out high-stress crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Determine 30-bar channel excluding the very current bar to prevent lookahead
        window_highs = highs[-self.channel_period - 1:-1]
        window_lows = lows[-self.channel_period - 1:-1]
        highest_high = max(window_highs)
        lowest_low = min(window_lows)
        midline = (highest_high + lowest_low) / 2.0
        channel_span = highest_high - lowest_low

        if lowest_low <= 0 or channel_span <= 0:
            return None

        width_pct = (channel_span / lowest_low) * 100.0

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Position management: fade exit at midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_midline_reached", "close": current_close, "midline": midline}
                )
            elif pos_dir == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_midline_reached", "close": current_close, "midline": midline}
                )
            return None

        # Range filter: stand down if volatility expands into a trend or is entirely dead
        if width_pct > self.max_width_pct or width_pct < self.min_width_pct:
            return None

        # Trend filter: only fade range edges when market context is neutral
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime not in ("neutral", "bull", "bear"):
            return None

        # Cross-asset volatility check: ensure BTC is not aggressively running
        btc_ret = abs(ctx.market.get("btc_return_pct", 0.0))
        if btc_ret > 2.8:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        edge_buffer = channel_span * 0.12

        # Long Setup: Price touches lower boundary and forms bullish rejection bar
        if current_close <= lowest_low + edge_buffer and current_close > current_open:
            if trend_regime != "bear":
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_scalp_channel_low_rejection",
                        "close": current_close,
                        "channel_low": lowest_low,
                        "channel_width_pct": width_pct,
                        "atr": atr,
                    }
                )

        # Short Setup: Price touches upper boundary and forms bearish rejection bar
        elif current_close >= highest_high - edge_buffer and current_close < current_open:
            if trend_regime != "bull":
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sol_scalp_channel_high_rejection",
                        "close": current_close,
                        "channel_high": highest_high,
                        "channel_width_pct": width_pct,
                        "atr": atr,
                    }
                )

        return None