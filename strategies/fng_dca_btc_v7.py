from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FearAccumulatorAlpha(Strategy):
    METADATA = {
        "name": "FearAccumulatorAlpha",
        "domain": "btc_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_period = 21
        self.fear_entry_thresh = 34.0
        self.greed_exit_thresh = 54.0
        self.cooldown_bars = 12
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        current_price = ctx.bar.close

        if rsi is None or ema is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit management
        if has_pos and pos_dir == "long":
            exit_reason = None
            if fg_index >= self.greed_exit_thresh:
                exit_reason = "sentiment_recovered_discount_gone"
            elif rsi >= 72.0:
                exit_reason = "rsi_overbought_swing_profit"

            if exit_reason:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "ema": round(ema, 2),
                    },
                )
            return None

        # Entry logic: long accumulation under fear
        if not has_pos:
            bars_since_exit = ctx.bar_index - self.last_exit_bar
            if bars_since_exit < self.cooldown_bars:
                return None

            # Enter when market is in fear regime AND price shows oversold bounce or recovery
            is_fear_regime = fg_index <= self.fear_entry_thresh
            is_oversold_bounce = (rsi <= 40.0 and current_price >= ctx.bar.open) or (
                fg_index <= 25.0 and rsi <= 45.0 and current_price > ema
            )

            if is_fear_regime and is_oversold_bounce:
                # Scale confidence by fear depth
                confidence = min(0.9, 0.55 + (self.fear_entry_thresh - fg_index) / 100.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_accumulation_bounce",
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi, 2),
                        "ema": round(ema, 2),
                        "price": current_price,
                    },
                )

        return None