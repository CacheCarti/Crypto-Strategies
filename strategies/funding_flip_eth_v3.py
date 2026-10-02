from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingRateUnwind(Strategy):
    METADATA = {
        "name": "Funding Rate Unwind Strategy",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 30
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.max_hold_bars = 8
        self.prev_funding = None
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 5)
        if len(closes) < self.warmup_bars:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        if self.prev_funding is None:
            self.prev_funding = current_funding
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        ema20 = self._ema(closes, 20)
        close_price = ctx.bar.close

        funding_flip_to_neg = self.prev_funding >= 0.0 and current_funding < 0.0
        funding_flip_to_pos = self.prev_funding <= 0.0 and current_funding > 0.0
        self.prev_funding = current_funding

        # Manage active position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time-based unwind exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_reached",
                        "bars_held": bars_held,
                        "funding": current_funding,
                        "rsi": rsi_val if rsi_val is not None else 50.0,
                        "price": close_price,
                    },
                )

            # Technical exit on momentum completion
            if pos_dir == "long" and rsi_val is not None and rsi_val > 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_tp_rsi_overbought",
                        "rsi": rsi_val,
                        "funding": current_funding,
                        "price": close_price,
                    },
                )
            elif pos_dir == "short" and rsi_val is not None and rsi_val < 32.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_tp_rsi_oversold",
                        "rsi": rsi_val,
                        "funding": current_funding,
                        "price": close_price,
                    },
                )

            # Signal flip exit / reversal
            if pos_dir == "long" and funding_flip_to_pos:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=480.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "flip_to_positive_shorts_capitulated",
                        "funding": current_funding,
                        "rsi": rsi_val if rsi_val is not None else 50.0,
                        "ema20": ema20 if ema20 is not None else close_price,
                        "price": close_price,
                    },
                )
            elif pos_dir == "short" and funding_flip_to_neg:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=480.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "flip_to_negative_longs_flushed",
                        "funding": current_funding,
                        "rsi": rsi_val if rsi_val is not None else 50.0,
                        "ema20": ema20 if ema20 is not None else close_price,
                        "price": close_price,
                    },
                )

            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Unwind Long Setup: Funding flips negative (longs flushed out) and RSI not heavily overbought
        if funding_flip_to_neg:
            if rsi_val is not None and rsi_val < 62.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=280.0,
                    take_profit_bps=480.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "funding_flip_neg_long_flush",
                        "funding": current_funding,
                        "rsi": rsi_val,
                        "ema20": ema20 if ema20 is not None else close_price,
                        "price": close_price,
                    },
                )

        # Unwind Short Setup: Funding flips positive (shorts capitulated) and RSI not heavily oversold
        if funding_flip_to_pos:
            if rsi_val is not None and rsi_val > 38.0:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=280.0,
                    take_profit_bps=480.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "funding_flip_pos_shorts_capitulated",
                        "funding": current_funding,
                        "rsi": rsi_val,
                        "ema20": ema20 if ema20 is not None else close_price,
                        "price": close_price,
                    },
                )

        return None