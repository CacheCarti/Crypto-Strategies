from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SOL Range Fade Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 48
        self.rsi_period = 10
        self.min_width_bps = 70.0
        self.max_width_bps = 170.0
        self.edge_threshold = 0.08
        self.cooldown_bars = 60
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
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

        if len(closes) < self.channel_period:
            return None

        # Compute Donchian range channel
        chan_high = max(highs[-self.channel_period:])
        chan_low = min(lows[-self.channel_period:])
        chan_range = chan_high - chan_low

        if chan_range <= 0.0:
            return None

        current_price = ctx.bar.close
        chan_mid = (chan_high + chan_low) / 2.0
        width_bps = (chan_range / chan_mid) * 10000.0
        rsi = self._rsi(closes, self.rsi_period)

        if rsi is None:
            return None

        # Manage open position: exit when reaching or crossing the channel midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_price >= chan_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_take_profit_midline_reached",
                        "price": current_price,
                        "chan_mid": chan_mid,
                        "rsi": round(rsi, 2),
                    },
                )
            elif direction == "short" and current_price <= chan_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_take_profit_midline_reached",
                        "price": current_price,
                        "chan_mid": chan_mid,
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Enforce multi-bar post-trade and post-entry cooldown to keep trade count bounded
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Regime filters: only trade calm/neutral sideways chop, avoid crisis or directional trends
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL") or ctx.regime in ("volatile", "crisis"):
            return None

        if trend_regime != "neutral" or crisis_score > 0.20:
            return None

        # Narrow range filter: only trade well-defined sideways channels
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        # Extreme edge boundaries
        lower_band = chan_low + (chan_range * self.edge_threshold)
        upper_band = chan_high - (chan_range * self.edge_threshold)

        # Long entry: price at lower boundary with extreme oversold RSI
        if current_price <= lower_band and rsi < 25.0:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (25.0 - rsi) / 50.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_low_fade_extreme_oversold",
                    "price": current_price,
                    "chan_low": chan_low,
                    "chan_mid": chan_mid,
                    "chan_width_bps": round(width_bps, 2),
                    "rsi": round(rsi, 2),
                },
            )

        # Short entry: price at upper boundary with extreme overbought RSI
        if current_price >= upper_band and rsi > 75.0:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (rsi - 75.0) / 50.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_high_fade_extreme_overbought",
                    "price": current_price,
                    "chan_high": chan_high,
                    "chan_mid": chan_mid,
                    "chan_width_bps": round(width_bps, 2),
                    "rsi": round(rsi, 2),
                },
            )

        return None