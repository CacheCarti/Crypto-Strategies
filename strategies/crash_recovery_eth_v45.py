from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryETH(Strategy):
    METADATA = {
        "name": "PanicRecoveryETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.045
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        prev_close = closes[-2]

        if ctx.has_position():
            if self.target_price > 0.0 and current_close >= self.target_price:
                self.target_price = 0.0
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "price": current_close,
                        "target": self.target_price,
                    }
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        rolling_return = (prev_close - closes[0]) / closes[0]
        recent_high = max(highs[-(self.lookback + 1):-1])
        drawdown = (prev_close - recent_high) / recent_high

        severe_drop = (rolling_return < self.drop_thresh) or (drawdown < self.drop_thresh)
        stabilized = (current_close > prev_high) and (current_close > ctx.bar.open)

        rsi = self._rsi(closes, period=14)
        rsi_val = rsi if rsi is not None else 50.0

        if severe_drop and stabilized and rsi_val < 50.0:
            self.flush_high = recent_high
            self.flush_low = min(lows[-(self.lookback + 1):])
            self.target_price = self.flush_low + 0.55 * (self.flush_high - self.flush_low)

            drop_magnitude = abs(min(rolling_return, drawdown))
            confidence = min(0.9, max(0.4, 0.4 + (drop_magnitude * 5.0)))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "flush_recovery_stabilization",
                    "rolling_return": round(rolling_return, 4),
                    "drawdown": round(drawdown, 4),
                    "rsi": round(rsi_val, 2),
                    "target_midpoint": round(self.target_price, 2),
                    "flush_low": round(self.flush_low, 2),
                }
            )

        return None