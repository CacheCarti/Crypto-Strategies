from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.rsi_period = 10
        self.max_width_pct = 1.3
        self.min_width_pct = 0.4
        self.cooldown_bars = 12
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 1:
            return None

        # Market regime filter from context (do not trade trending / crisis regimes)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if regime in ("HIGH_VOL", "CRISIS", "MELTDOWN") or trend_regime != "neutral":
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_invalidation", "regime": regime, "trend_regime": trend_regime}
                )
            return None

        # Exclude current bar from channel extremes to prevent lookahead / immediate breakout contamination
        ch_high = max(highs[-self.channel_period - 1:-1])
        ch_low = min(lows[-self.channel_period - 1:-1])
        ch_mid = (ch_high + ch_low) / 2.0
        width = ch_high - ch_low

        if ch_mid <= 0:
            return None

        width_pct = (width / ch_mid) * 100.0
        current_close = closes[-1]
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Manage active position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If the channel widens drastically (trend breakout), stand down and cut trade
            if width_pct > self.max_width_pct * 1.3:
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={"reason": "channel_expansion_stop", "width_pct": width_pct}
                )
            # Reached or crossed midline
            if pos_dir == "long" and current_close >= ch_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "midline_reached_long", "price": current_close, "ch_mid": ch_mid, "rsi": rsi}
                )
            elif pos_dir == "short" and current_close <= ch_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "midline_reached_short", "price": current_close, "ch_mid": ch_mid, "rsi": rsi}
                )
            return None

        # Check cooldown to maintain strictly 20-80 trades over 17,000 bars
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Tight channel restriction (chop tape)
        if not (self.min_width_pct <= width_pct <= self.max_width_pct):
            return None

        # Long: price piercing near/below channel floor, RSI confirming exhaustion (turning back up)
        if current_close <= ch_low + (width * 0.12) and rsi < 33:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_floor",
                    "price": current_close,
                    "ch_low": ch_low,
                    "ch_mid": ch_mid,
                    "ch_high": ch_high,
                    "width_pct": width_pct,
                    "rsi": rsi
                }
            )

        # Short: price piercing near/above channel ceiling, RSI confirming exhaustion
        if current_close >= ch_high - (width * 0.12) and rsi > 67:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_ceiling",
                    "price": current_close,
                    "ch_low": ch_low,
                    "ch_mid": ch_mid,
                    "ch_high": ch_high,
                    "width_pct": width_pct,
                    "rsi": rsi
                }
            )

        return None