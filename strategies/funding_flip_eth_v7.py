from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingUnwindReversal(Strategy):
    METADATA = {
        "name": "Funding Unwind Reversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 5
        self.last_funding = None
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        curr_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position management: time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if self.entry_bar > 0 and bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_hold_horizon_reached",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "funding": curr_funding
                    }
                )
            self.last_funding = curr_funding
            return None

        # Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            self.last_funding = curr_funding
            return None

        # Detect funding sign flips
        signal = None
        if self.last_funding is not None:
            # Positive -> Negative flip: longs capitulated, look for bottom relief bounce
            if self.last_funding >= 0.0 and curr_funding < -0.00001:
                if rsi <= 55.0:
                    self.entry_bar = ctx.bar_index
                    confidence = 0.65 if rsi < 40.0 else 0.55
                    signal = ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=240.0,
                        take_profit_bps=420.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "funding_flip_long_flush_relief",
                            "prev_funding": self.last_funding,
                            "curr_funding": curr_funding,
                            "rsi": round(rsi, 2)
                        }
                    )

            # Negative -> Positive flip: shorts capitulated / late longs chasing, fade local top
            elif self.last_funding <= 0.0 and curr_funding > 0.00001:
                if rsi >= 45.0:
                    self.entry_bar = ctx.bar_index
                    confidence = 0.65 if rsi > 60.0 else 0.55
                    signal = ctx.signal(
                        "short",
                        confidence=confidence,
                        stop_loss_bps=240.0,
                        take_profit_bps=420.0,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "funding_flip_short_squeeze_fade",
                            "prev_funding": self.last_funding,
                            "curr_funding": curr_funding,
                            "rsi": round(rsi, 2)
                        }
                    )

        self.last_funding = curr_funding
        return signal