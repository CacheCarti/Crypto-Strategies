from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.max_width_pct = 0.018  # Channel must be <= 1.8% to be considered chop/range
        self.min_width_pct = 0.006  # Channel must be >= 0.6% to cover friction (12 bps)
        self.touch_buffer_pct = 0.12 # Must be within 12% of the channel extreme

    def _rsi(self, closes, period=10):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.channel_period + 5:
            return None

        # Stand down during crisis or meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN") or ctx.regime in ("crisis", "volatile"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_risk_exit"})
            return None

        # Prior 30-bar channel (excluding current bar to avoid lookahead bias)
        chan_high = max(highs[-self.channel_period - 1:-1])
        chan_low = min(lows[-self.channel_period - 1:-1])
        midline = (chan_high + chan_low) / 2.0
        width = chan_high - chan_low

        if midline <= 0:
            return None

        width_pct = width / midline
        current_close = ctx.bar.close

        # Position management: exit if reached midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_midline",
                    "close": current_close,
                    "midline": midline
                })
            elif pos_dir == "short" and current_close <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_midline",
                    "close": current_close,
                    "midline": midline
                })
            return None

        # Mandatory cooldown after any exit to limit trade count and avoid re-entering chop whipsaws
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Stand down when the channel widens past the threshold (trend starting) or is too compressed
        if width_pct > self.max_width_pct or width_pct < self.min_width_pct:
            return None

        # Filter out strong directional macro trends to avoid fading trending expansion
        trend_regime = ctx.market.get("trend_regime", "neutral")
        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        edge_distance = width * self.touch_buffer_pct

        # Long setup: price near channel low, RSI oversold-leaning, not in a bear trend
        if current_close <= (chan_low + edge_distance) and rsi < 36.0 and trend_regime != "bear":
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_low_rsi_reversion",
                    "close": current_close,
                    "chan_low": chan_low,
                    "midline": midline,
                    "rsi": rsi,
                    "width_pct": width_pct
                }
            )

        # Short setup: price near channel high, RSI overbought-leaning, not in a bull trend
        if current_close >= (chan_high - edge_distance) and rsi > 64.0 and trend_regime != "bull":
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_high_rsi_reversion",
                    "close": current_close,
                    "chan_high": chan_high,
                    "midline": midline,
                    "rsi": rsi,
                    "width_pct": width_pct
                }
            )

        return None