from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FearAccumulationSwing(Strategy):
    METADATA = {
        "name": "FearAccumulationSwing",
        "domain": "btc_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 34
        self.fear_threshold = 36
        self.exit_greed_threshold = 55
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi_series(self, closes, period=14):
        if len(closes) < period + 2:
            return None, None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        avg_gain_prev = sum(gains[-period - 1:-1]) / period
        avg_loss_prev = sum(losses[-period - 1:-1]) / period
        rs_prev = avg_gain_prev / avg_loss_prev if avg_loss_prev > 0 else 100.0
        rsi_prev = 100.0 - (100.0 / (1.0 + rs_prev)) if avg_loss_prev > 0 else 100.0

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        rs_curr = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi_curr = 100.0 - (100.0 / (1.0 + rs_curr)) if avg_loss > 0 else 100.0

        return rsi_prev, rsi_curr

    def _ema(self, values, period):
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

        current_price = ctx.bar.close
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        rsi_prev, rsi_curr = self._rsi_series(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi_curr is None or ema_fast is None or ema_slow is None:
            return None

        has_pos = ctx.has_position()

        # Exit conditions for open Long
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            exit_reason = None

            if fear_greed >= self.exit_greed_threshold and rsi_curr > 52.0:
                exit_reason = "fear_greed_recovered_target"
            elif rsi_curr >= 72.0:
                exit_reason = "rsi_overbought_exhaustion"
            elif bars_held >= 6 and current_price < ema_slow and rsi_curr < 42.0:
                exit_reason = "trend_support_breakdown"

            if exit_reason:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed": fear_greed,
                        "rsi": round(rsi_curr, 2),
                        "bars_held": bars_held,
                        "close": current_price
                    }
                )
            return None

        # Check cooldown before opening new position
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Price setup: Fear regime OR technical oversold with bullish momentum turn
        is_fear_zone = fear_greed <= self.fear_threshold
        rsi_oversold_bounce = (rsi_curr < 44.0 and rsi_curr > rsi_prev and rsi_prev < 38.0)
        price_recovering = current_price >= ema_fast * 0.995

        entry_trigger = False
        reason = ""

        if is_fear_zone and (rsi_curr < 46.0 and rsi_curr > rsi_prev):
            entry_trigger = True
            reason = "fear_accumulation_rsi_turn"
        elif rsi_oversold_bounce and price_recovering:
            entry_trigger = True
            reason = "technical_oversold_reversal"

        if entry_trigger:
            self.entry_bar = ctx.bar_index
            confidence = 0.80 if is_fear_zone else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "fear_greed": fear_greed,
                    "rsi": round(rsi_curr, 2),
                    "ema_fast": round(ema_fast, 2),
                    "close": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index