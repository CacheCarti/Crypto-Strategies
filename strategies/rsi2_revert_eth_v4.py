from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class ConnorsRsiSnapback(Strategy):
    METADATA = {
        "name": "Connors RSI Snapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 2
        self.long_entry_thresh = 5.0
        self.long_exit_thresh = 70.0
        self.short_entry_thresh = 95.0
        self.short_exit_thresh = 30.0
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int = 2) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_price = ctx.bar.close

        # Position exit management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and rsi >= self.long_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi2_snapback_take_profit",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )
            if direction == "short" and rsi <= self.short_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi2_snapback_cover",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )
            return None

        # Hard multi-bar cooldown filter after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Deep oversold snapback entry
        if rsi <= self.long_entry_thresh:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "rsi2_extreme_oversold_entry",
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        # Deep overbought snapback short entry
        if rsi >= self.short_entry_thresh:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "rsi2_extreme_overbought_entry",
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index