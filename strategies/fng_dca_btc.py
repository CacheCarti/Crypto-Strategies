from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFearAccumulator(Strategy):
    METADATA = {
        "name": "BTC Fear Accumulator",
        "domain": "btc_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 1000.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.last_entry_bar = -999

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

    def _ema(self, values, period) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        rsi_current = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)
        ema20 = self._ema(closes, 20)
        current_close = ctx.bar.close

        if rsi_current is None or rsi_prev is None or ema20 is None:
            return None

        # Manage open position exits
        if ctx.has_position():
            # Exit rule 1: Fear discount resolved (F&G index recovers >= 55)
            # Exit rule 2: Short-term momentum overbought exhaustion (RSI >= 72)
            if fg_index >= 55.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "fear_greed_normalized_exit",
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi_current, 2),
                        "price": current_close,
                    },
                )
            elif rsi_current >= 72.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi_overbought_swing_exit",
                        "fear_greed_index": fg_index,
                        "rsi": round(rsi_current, 2),
                        "price": current_close,
                    },
                )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Accumulate during fear (< 35) when price pulls back into oversold/turning territory
        is_fear_regime = fg_index < 35.0
        rsi_oversold_bounce = (rsi_current < 38.0) or (rsi_prev < 32.0 and rsi_current > rsi_prev)

        if is_fear_regime and rsi_oversold_bounce:
            # Scale confidence based on extremity of fear & oversold level
            fear_score = max(0.0, min(1.0, (35.0 - fg_index) / 25.0))
            rsi_score = max(0.0, min(1.0, (40.0 - rsi_current) / 20.0))
            confidence = round(0.55 + 0.25 * (fear_score * 0.6 + rsi_score * 0.4), 2)

            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, confidence),
                stop_loss_bps=500.0,
                take_profit_bps=1000.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "fear_regime_rsi_dip_accumulation",
                    "fear_greed_index": fg_index,
                    "rsi": round(rsi_current, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "ema20": round(ema20, 2),
                    "price": current_close,
                },
            )

        return None