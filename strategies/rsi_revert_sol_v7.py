from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class SolRsiMeanReversion(Strategy):
    METADATA = {
        "name": "SolRsiMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.oversold = 24.0
        self.overbought = 76.0
        self.exit_long_rsi = 55.0
        self.exit_short_rsi = 45.0
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 2:
            return None

        rsi_curr = self._rsi(closes, self.rsi_period)
        rsi_prev = self._rsi(closes[:-1], self.rsi_period)

        if rsi_curr is None or rsi_prev is None:
            return None

        # Position management: exit once RSI returns to the neutral zone
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi_curr >= self.exit_long_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": "rsi_reverted_to_neutral_high", "rsi": rsi_curr, "rsi_prev": rsi_prev}
                )
            elif pos_dir == "short" and rsi_curr <= self.exit_short_rsi:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": "rsi_reverted_to_neutral_low", "rsi": rsi_curr, "rsi_prev": rsi_prev}
                )
            return None

        # Strict multi-bar cooldown after any exit or recent entry
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Long entry: extreme oversold bounce (RSI was <= 24 and crosses upward)
        if rsi_prev <= self.oversold and rsi_curr > rsi_prev + 0.5:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "rsi_extreme_oversold_bounce",
                    "rsi": rsi_curr,
                    "rsi_prev": rsi_prev,
                    "oversold_threshold": self.oversold,
                    "close": ctx.bar.close
                }
            )

        # Short entry: extreme overbought rejection (RSI was >= 76 and turns downward)
        if rsi_prev >= self.overbought and rsi_curr < rsi_prev - 0.5:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "rsi_extreme_overbought_rejection",
                    "rsi": rsi_curr,
                    "rsi_prev": rsi_prev,
                    "overbought_threshold": self.overbought,
                    "close": ctx.bar.close
                }
            )

        return None