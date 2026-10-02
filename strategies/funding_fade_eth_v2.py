from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
from collections import deque
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "Funding Rate Contrarian Squeeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = deque(maxlen=6)
        self.cooldown_until_bar = 0
        self.bars_in_position = 0
        self.cooldown_bars = 5
        self.rsi_period = 14
        self.ema_period = 21
        
        # Funding rate thresholds (per 8h rate)
        self.pos_funding_extreme = 0.00025  # +0.025% per 8h
        self.neg_funding_extreme = -0.00015 # -0.015% per 8h
        self.neutral_exit_long = 0.00005
        self.neutral_exit_short = -0.00003

    def _rsi(self, closes, period=14) -> float:
        if len(closes) < period + 1:
            return 50.0
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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)

        if len(self.funding_history) < 3:
            return None

        avg_funding_3 = sum(list(self.funding_history)[-3:]) / 3.0
        rsi = self._rsi(closes, self.rsi_period)
        ema_val = self._ema(closes, self.ema_period)
        current_close = ctx.bar.close

        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Track active position holding
        if ctx.has_position():
            self.bars_in_position += 1
            direction = ctx.position_direction()

            # Exit logic: Funding normalizes or technical reversal threshold reached
            if direction == "long":
                if avg_funding_3 >= self.neutral_exit_long or rsi > 70.0 or self.bars_in_position > 12:
                    self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_funding_normalized_or_overbought",
                            "avg_funding": avg_funding_3,
                            "rsi": rsi,
                            "bars_held": self.bars_in_position,
                            "price": current_close
                        }
                    )
            elif direction == "short":
                if avg_funding_3 <= self.neutral_exit_short or rsi < 30.0 or self.bars_in_position > 12:
                    self.cooldown_until_bar = ctx.bar_index + self.cooldown_bars
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_funding_normalized_or_oversold",
                            "avg_funding": avg_funding_3,
                            "rsi": rsi,
                            "bars_held": self.bars_in_position,
                            "price": current_close
                        }
                    )
            return None

        # Cooldown guard for new entries
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Skip during severe market breakdown
        if crisis_score > 0.70:
            return None

        # Long Entry: Negative funding persistence (shorts crowded & paying) + price stabilizing
        if avg_funding_3 < self.neg_funding_extreme and current_funding < 0.0:
            # Price filter: Not in an uncontrollable freefall; moderate RSI bouncing or holding near EMA
            if 28.0 < rsi < 56.0 and (ema_val is not None and current_close >= ema_val * 0.985):
                self.bars_in_position = 0
                confidence = min(0.85, 0.55 + abs(avg_funding_3) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "crowded_short_funding_squeeze_long",
                        "avg_funding_3": avg_funding_3,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "ema": ema_val,
                        "price": current_close
                    }
                )

        # Short Entry: Positive funding persistence (longs crowded & paying) + price showing resistance
        if avg_funding_3 > self.pos_funding_extreme and current_funding > 0.0:
            # Price filter: Not parabolic runaway; RSI upper-mid but not totally exhausted
            if 46.0 < rsi < 72.0 and (ema_val is not None and current_close <= ema_val * 1.015):
                self.bars_in_position = 0
                confidence = min(0.85, 0.55 + avg_funding_3 * 1000.0)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "crowded_long_funding_fade_short",
                        "avg_funding_3": avg_funding_3,
                        "current_funding": current_funding,
                        "rsi": rsi,
                        "ema": ema_val,
                        "price": current_close
                    }
                )

        return None