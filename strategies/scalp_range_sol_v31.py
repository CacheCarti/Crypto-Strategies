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
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _rsi(self, closes, period=7):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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

        if len(closes) < self.channel_period:
            return None

        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        ch_high = max(recent_highs)
        ch_low = min(recent_lows)
        ch_mid = (ch_high + ch_low) / 2.0

        if ch_mid <= 0:
            return None

        range_bps = ((ch_high - ch_low) / ch_mid) * 10000.0
        price = ctx.bar.close

        # Position exit logic: mean reversion target hit at midline
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            if direction == "long" and price >= ch_mid:
                return ctx.signal(
                    "flat", 
                    confidence=0.7, 
                    metadata={"reason": "long_midline_reached", "price": price, "midline": ch_mid, "bars_held": bars_held}
                )
            elif direction == "short" and price <= ch_mid:
                return ctx.signal(
                    "flat", 
                    confidence=0.7, 
                    metadata={"reason": "short_midline_reached", "price": price, "midline": ch_mid, "bars_held": bars_held}
                )
            return None

        # Post-exit mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Stand down in trending or volatile conditions (range too wide) or dead flat (no profit room)
        # 70 bps to 220 bps is the prime chop window for SOL 5m
        if range_bps < 70.0 or range_bps > 220.0:
            return None

        # Stand down during crisis or high volatility regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        rsi = self._rsi(closes, period=7)
        ch_span = ch_high - ch_low
        if ch_span <= 0:
            return None

        pos_in_range = (price - ch_low) / ch_span

        # Fade extremes: long within lower 12% + oversold bounce check
        if pos_in_range <= 0.12 and rsi < 34.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.68,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low_oversold",
                    "price": price,
                    "ch_low": ch_low,
                    "ch_mid": ch_mid,
                    "range_bps": range_bps,
                    "rsi": rsi,
                    "pos_in_range": pos_in_range
                }
            )

        # Fade extremes: short within upper 12% + overbought reversal check
        if pos_in_range >= 0.88 and rsi > 66.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.68,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high_overbought",
                    "price": price,
                    "ch_high": ch_high,
                    "ch_mid": ch_mid,
                    "range_bps": range_bps,
                    "rsi": rsi,
                    "pos_in_range": pos_in_range
                }
            )

        return None