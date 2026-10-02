from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ETHFundingFlipUnwind(Strategy):
    METADATA = {
        "name": "ETH Funding Flip Positioning Unwind",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_rsi = 14
        self.period_fast_ema = 12
        self.period_slow_ema = 34
        self.cooldown_bars = 6
        self.last_trade_bar = -100
        self.last_exit_bar = -100
        self.prev_funding: Optional[float] = None

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
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 10)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        curr_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        if self.prev_funding is None:
            self.prev_funding = curr_funding
            return None

        rsi = self._rsi(closes, self.period_rsi)
        ema_fast = self._ema(closes, self.period_fast_ema)
        ema_slow = self._ema(closes, self.period_slow_ema)

        if rsi is None or ema_fast is None or ema_slow is None:
            self.prev_funding = curr_funding
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        bars_since_trade = ctx.bar_index - self.last_trade_bar
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Check flip transitions with slight deadband to prevent noise triggering
        pos_to_neg_flip = (self.prev_funding >= 0.0) and (curr_funding < -0.00001)
        neg_to_pos_flip = (self.prev_funding <= 0.0) and (curr_funding > 0.00001)
        self.prev_funding = curr_funding

        # Exit management for active position
        if ctx.has_position():
            direction = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if direction == "long":
                if rsi > 72.0:
                    should_exit = True
                    exit_reason = "rsi_overbought_take_profit"
                elif curr_funding > 0.0003:
                    should_exit = True
                    exit_reason = "funding_overheated_positive"
            elif direction == "short":
                if rsi < 28.0:
                    should_exit = True
                    exit_reason = "rsi_oversold_take_profit"
                elif curr_funding < -0.0003:
                    should_exit = True
                    exit_reason = "funding_overheated_negative"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding": curr_funding,
                        "price": current_price,
                        "bars_held": bars_since_trade,
                    },
                )
            return None

        # Guard against crisis melt and enforce cooldowns
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if bars_since_exit < self.cooldown_bars or bars_since_trade < self.cooldown_bars:
            return None

        # LONG SETUP: Positive -> Negative flip (Long flush completed, shorts crowded / aggressive at low)
        if pos_to_neg_flip and rsi < 60.0:
            trend_alignment = 1.0 if current_price >= ema_slow else 0.8
            confidence = min(0.85, 0.55 * trend_alignment + (50.0 - min(rsi, 50.0)) / 100.0)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_flip_positive_to_negative_long_flush",
                    "funding_curr": curr_funding,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": current_price,
                },
            )

        # SHORT SETUP: Negative -> Positive flip (Shorts capitulated, late longs chasing into resistance)
        if neg_to_pos_flip and rsi > 40.0:
            trend_alignment = 1.0 if current_price <= ema_slow else 0.8
            confidence = min(0.85, 0.55 * trend_alignment + (max(rsi, 50.0) - 50.0) / 100.0)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_flip_negative_to_positive_short_squeeze_exhaustion",
                    "funding_curr": curr_funding,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index