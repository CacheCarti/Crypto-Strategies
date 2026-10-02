from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingUnwindReversion(Strategy):
    METADATA = {
        "name": "ETH Funding Rate Unwind Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.prev_funding: Optional[float] = None
        self.last_trade_bar: int = -100
        self.cooldown_bars: int = 5
        self.rsi_period: int = 14
        self.ema_period: int = 21
        self.max_hold_bars: int = 14

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        curr_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Skip entering in severe market crisis
        if crisis_score > 0.75:
            self.prev_funding = curr_funding
            return None

        rsi = self._rsi(closes, self.rsi_period)
        ema21 = self._ema(closes, self.ema_period)
        if rsi is None or ema21 is None:
            self.prev_funding = curr_funding
            return None

        price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Manage open position exit logic
        if has_pos:
            bars_held = ctx.bar_index - self.last_trade_bar
            # Overextended momentum exit or max hold time
            if pos_dir == "long" and (rsi > 68.0 or bars_held >= self.max_hold_bars):
                self.prev_funding = curr_funding
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_exhaustion_or_time_exit", "rsi": rsi, "bars_held": bars_held}
                )
            elif pos_dir == "short" and (rsi < 32.0 or bars_held >= self.max_hold_bars):
                self.prev_funding = curr_funding
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_exhaustion_or_time_exit", "rsi": rsi, "bars_held": bars_held}
                )

        # Enforce entry cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            self.prev_funding = curr_funding
            return None

        # Check funding flip event
        signal = None
        if self.prev_funding is not None and not has_pos:
            # Positive -> Negative flip: Longs flushed, local capitulation -> Go LONG
            flipped_negative = (self.prev_funding >= -0.00001 and curr_funding < -0.00003) or (self.prev_funding > 0.0 and curr_funding <= -0.00001)
            # Negative -> Positive flip: Shorts squeezed/capitulated, local euphoria -> Go SHORT
            flipped_positive = (self.prev_funding <= 0.00001 and curr_funding > 0.00003) or (self.prev_funding < 0.0 and curr_funding >= 0.00001)

            if flipped_negative and rsi < 55.0 and fg_index < 75.0:
                conf = 0.65 if rsi < 42.0 else 0.55
                self.last_trade_bar = ctx.bar_index
                signal = ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_flip_neg_long_unwind",
                        "rsi": round(rsi, 2),
                        "funding_prev": self.prev_funding,
                        "funding_curr": curr_funding,
                        "price": price,
                        "ema21": round(ema21, 2)
                    }
                )
            elif flipped_positive and rsi > 45.0 and fg_index > 25.0:
                conf = 0.65 if rsi > 58.0 else 0.55
                self.last_trade_bar = ctx.bar_index
                signal = ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_flip_pos_short_unwind",
                        "rsi": round(rsi, 2),
                        "funding_prev": self.prev_funding,
                        "funding_curr": curr_funding,
                        "price": price,
                        "ema21": round(ema21, 2)
                    }
                )

        self.prev_funding = curr_funding
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index