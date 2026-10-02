from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class RsiSnapback(Strategy):
    METADATA = {
        "name": "RSI Extreme Snapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 10800,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 4
        self.oversold_thresh = 6.0
        self.overbought_thresh = 94.0
        self.exit_long_thresh = 55.0
        self.exit_short_thresh = 45.0
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def _rsi(self, closes, period: int) -> Optional[float]:
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
        if avg_gain == 0.0:
            return 0.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 5:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        pos_dir = ctx.position_direction()

        if pos_dir == "long":
            if rsi >= self.exit_long_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_long_snapback_target_reached",
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        if pos_dir == "short":
            if rsi <= self.exit_short_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_short_snapback_target_reached",
                        "rsi": round(rsi, 2),
                        "price": ctx.bar.close,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if rsi <= self.oversold_thresh:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "rsi_extreme_oversold_snapback",
                    "rsi": round(rsi, 2),
                    "price": ctx.bar.close,
                },
            )

        if rsi >= self.overbought_thresh:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "rsi_extreme_overbought_snapback",
                    "rsi": round(rsi, 2),
                    "price": ctx.bar.close,
                },
            )

        return None