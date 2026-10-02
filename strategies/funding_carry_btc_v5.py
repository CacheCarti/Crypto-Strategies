from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarryTrend(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Trend",
        "domain": "btc_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_fast_period = 21
        self.trend_slow_period = 55
        self.funding_lookback = 12
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_slow_period + 5)
        if len(closes) < self.trend_slow_period:
            return None

        # Fetch and smooth funding rate
        raw_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(raw_funding)
        if len(self.funding_history) > self.funding_lookback * 2:
            self.funding_history.pop(0)

        recent_funding = self.funding_history[-self.funding_lookback:]
        smoothed_funding = sum(recent_funding) / len(recent_funding)

        ema_fast = self._ema(closes, self.trend_fast_period)
        ema_slow = self._ema(closes, self.trend_slow_period)
        if ema_fast is None or ema_slow is None:
            return None

        current_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar

        # Position Management / Exits
        if has_pos:
            if pos_dir == "long":
                # Long carry exhaustion or trend breakdown
                if smoothed_funding > 0.00035 or current_price < ema_slow * 0.985:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_carry_exhaustion_or_trend_break",
                            "smoothed_funding": round(smoothed_funding, 6),
                            "ema_slow": round(ema_slow, 2),
                            "price": round(current_price, 2)
                        }
                    )
            elif pos_dir == "short":
                # Short carry exhaustion or trend breakout
                if smoothed_funding < -0.00005 or current_price > ema_slow * 1.015:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_carry_exhaustion_or_trend_break",
                            "smoothed_funding": round(smoothed_funding, 6),
                            "ema_slow": round(ema_slow, 2),
                            "price": round(current_price, 2)
                        }
                    )
            return None

        # Cooldown guard
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Setup 1: Long carry setup (Negative or low funding with positive trend support)
        # Shorts pay longs + price above fast EMA and fast EMA > slow EMA
        long_carry_bias = smoothed_funding <= -0.00002 and current_price > ema_fast
        long_trend_carry = smoothed_funding < 0.00010 and ema_fast > ema_slow and current_price > ema_fast

        if long_carry_bias or long_trend_carry:
            conf = 0.75 if smoothed_funding < -0.00003 else 0.65
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_negative_trend_aligned_long",
                    "smoothed_funding": round(smoothed_funding, 6),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(current_price, 2)
                }
            )

        # Setup 2: Short carry tilt (Overheated long funding + price below slow EMA)
        # Longs pay shorts heavily + trend confirmation
        short_carry_setup = smoothed_funding >= 0.00025 and current_price < ema_fast and ema_fast < ema_slow

        if short_carry_setup:
            conf = 0.70 if smoothed_funding >= 0.00035 else 0.60
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_overheated_trend_aligned_short",
                    "smoothed_funding": round(smoothed_funding, 6),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": round(current_price, 2)
                }
            )

        return None