from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class ConnorsRsiSnapback(Strategy):
    METADATA = {
        "name": "Connors RSI(2) Snapback",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 2
        self.long_entry_thresh = 4.0
        self.long_exit_thresh = 70.0
        self.short_entry_thresh = 96.0
        self.short_exit_thresh = 30.0
        self.cooldown_bars = 16
        self.last_exit_bar = -999

    def _rsi(self, closes, period: int = 2) -> Optional[float]:
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
        closes = ctx.closes(25)
        if len(closes) < self.rsi_period + 2:
            return None

        rsi2 = self._rsi(closes, self.rsi_period)
        if rsi2 is None:
            return None

        current_price = ctx.bar.close
        pos_dir = ctx.position_direction()

        # Exit conditions
        if pos_dir == "long":
            if rsi2 >= self.long_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi2_mean_reversion_target_hit",
                        "rsi2": round(rsi2, 2),
                        "price": current_price,
                    },
                )
            return None

        if pos_dir == "short":
            if rsi2 <= self.short_exit_thresh:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "rsi2_mean_reversion_target_hit",
                        "rsi2": round(rsi2, 2),
                        "price": current_price,
                    },
                )
            return None

        # Mandatory cooldown check after prior trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Tightened entry conditions to avoid overtrading and friction drag
        if rsi2 <= self.long_entry_thresh:
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "rsi2_ultra_oversold_snapback",
                    "rsi2": round(rsi2, 2),
                    "price": current_price,
                },
            )

        if rsi2 >= self.short_entry_thresh:
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "rsi2_ultra_overbought_snapback",
                    "rsi2": round(rsi2, 2),
                    "price": current_price,
                },
            )

        return None