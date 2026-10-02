from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingRateUnwindSwing(Strategy):
    METADATA = {
        "name": "FundingRateUnwindSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.prev_funding: Optional[float] = None
        self.last_exit_bar: int = -100
        self.cooldown_bars: int = 4
        self.rsi_period: int = 14
        self.zscore_period: int = 20

    def _rsi(self, closes, period=14) -> Optional[float]:
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

    def _zscore(self, values, period=20) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        std = math.sqrt(variance)
        if std == 0.0:
            return 0.0
        return (values[-1] - mean) / std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        curr_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        rsi = self._rsi(closes, self.rsi_period)
        zscore = self._zscore(closes, self.zscore_period)
        price = ctx.bar.close

        if rsi is None or zscore is None:
            self.prev_funding = curr_funding
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            self.prev_funding = curr_funding
            return None

        if self.prev_funding is None:
            self.prev_funding = curr_funding
            return None

        # Detect Sign Flips & Major Unwinds
        long_flip = (self.prev_funding >= 0.0 and curr_funding < 0.0) or (
            self.prev_funding > 0.00015 and curr_funding <= 0.00003
        )
        short_flip = (self.prev_funding <= 0.0 and curr_funding > 0.0) or (
            self.prev_funding < -0.00015 and curr_funding >= -0.00003
        )

        signal: Optional[Signal] = None

        if not ctx.has_position():
            # Long setup: Longs flushed out / funding flipped negative
            if long_flip and rsi < 62.0 and zscore < 1.2:
                conf = 0.70
                if rsi < 42.0:
                    conf += 0.15
                if fear_greed < 40:
                    conf += 0.10
                conf = min(0.95, conf)

                signal = ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_unwind_long_flush",
                        "curr_funding": curr_funding,
                        "prev_funding": self.prev_funding,
                        "rsi": round(rsi, 2),
                        "zscore": round(zscore, 2),
                        "fear_greed": fear_greed,
                        "price": price,
                    },
                )

            # Short setup: Shorts capitulated / funding flipped positive
            elif short_flip and rsi > 38.0 and zscore > -1.2:
                conf = 0.70
                if rsi > 58.0:
                    conf += 0.15
                if fear_greed > 60:
                    conf += 0.10
                conf = min(0.95, conf)

                signal = ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_unwind_short_squeeze_top",
                        "curr_funding": curr_funding,
                        "prev_funding": self.prev_funding,
                        "rsi": round(rsi, 2),
                        "zscore": round(zscore, 2),
                        "fear_greed": fear_greed,
                        "price": price,
                    },
                )

        else:
            # Active Position Management: early exit on extreme counter-momentum
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (rsi > 78.0 or short_flip):
                signal = ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_exhaustion_or_funding_reflip",
                        "rsi": round(rsi, 2),
                        "curr_funding": curr_funding,
                        "price": price,
                    },
                )
            elif pos_dir == "short" and (rsi < 22.0 or long_flip):
                signal = ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_exhaustion_or_funding_reflip",
                        "rsi": round(rsi, 2),
                        "curr_funding": curr_funding,
                        "price": price,
                    },
                )

        self.prev_funding = curr_funding
        return signal