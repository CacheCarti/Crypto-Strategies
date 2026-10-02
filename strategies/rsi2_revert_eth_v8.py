from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class RsiSnapbackConnors(Strategy):
    METADATA = {
        "name": "RsiSnapbackConnors",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 20,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 3
        self.long_entry_rsi = 5.0
        self.long_exit_rsi = 60.0
        self.short_entry_rsi = 95.0
        self.short_exit_rsi = 40.0
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_price = ctx.bar.close
        pos_dir = ctx.position_direction()

        # Manage open position exits
        if ctx.has_position():
            if pos_dir == "long" and rsi >= self.long_exit_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi3_rebound_exit_long",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )
            elif pos_dir == "short" and rsi <= self.short_exit_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi3_rebound_exit_short",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    },
                )
            return None

        # Hard multi-bar cooldown guard after prior exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Deep oversold snapback entry
        if rsi <= self.long_entry_rsi:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "rsi3_deep_oversold_entry",
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        # Deep overbought snapback entry
        if rsi >= self.short_entry_rsi:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "rsi3_deep_overbought_entry",
                    "rsi": round(rsi, 2),
                    "price": current_price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index