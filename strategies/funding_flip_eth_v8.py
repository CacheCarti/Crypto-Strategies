from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateFlipReversion(Strategy):
    METADATA = {
        "name": "Funding Rate Flip Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.prev_funding = None

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        curr_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        price = ctx.bar.close

        if self.prev_funding is None:
            self.prev_funding = curr_funding
            return None

        flipped_neg = (self.prev_funding >= 0.0 and curr_funding < 0.0)
        flipped_pos = (self.prev_funding <= 0.0 and curr_funding > 0.0)

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time-based hold expiration
            if bars_held >= self.max_hold_bars:
                self.prev_funding = curr_funding
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_time_reached",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "funding_rate": curr_funding,
                        "price": price,
                    }
                )

            # Signal exhaustion exits
            if direction == "long" and (rsi > 68.0 or flipped_pos):
                self.prev_funding = curr_funding
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_exhaustion_or_funding_flipped_pos",
                        "rsi": round(rsi, 2),
                        "funding_rate": curr_funding,
                        "price": price,
                    }
                )

            if direction == "short" and (rsi < 32.0 or flipped_neg):
                self.prev_funding = curr_funding
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_exhaustion_or_funding_flipped_neg",
                        "rsi": round(rsi, 2),
                        "funding_rate": curr_funding,
                        "price": price,
                    }
                )

            self.prev_funding = curr_funding
            return None

        # Entry Logic (Gated by cooldown)
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.prev_funding = curr_funding
            return None

        # Long Trigger: Funding flipped negative (longs flushed) or deep negative funding with oversold RSI
        long_setup = (flipped_neg and rsi < 55.0) or (curr_funding < -0.0001 and rsi < 40.0)
        # Short Trigger: Funding flipped positive (shorts capitulated) or high positive funding with overbought RSI
        short_setup = (flipped_pos and rsi > 45.0) or (curr_funding > 0.0001 and rsi > 60.0)

        sig = None
        if long_setup and not short_setup:
            conf = 0.75 if flipped_neg else 0.65
            self.entry_bar = ctx.bar_index
            sig = ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_unwind_long_flush" if flipped_neg else "extreme_negative_funding_oversold",
                    "funding_rate": curr_funding,
                    "prev_funding": self.prev_funding,
                    "rsi": round(rsi, 2),
                    "price": price,
                }
            )
        elif short_setup and not long_setup:
            conf = 0.75 if flipped_pos else 0.65
            self.entry_bar = ctx.bar_index
            sig = ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_unwind_short_capitulation" if flipped_pos else "extreme_positive_funding_overbought",
                    "funding_rate": curr_funding,
                    "prev_funding": self.prev_funding,
                    "rsi": round(rsi, 2),
                    "price": price,
                }
            )

        self.prev_funding = curr_funding
        return sig