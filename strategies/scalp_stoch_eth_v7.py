from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class StochasticScalpReversion(Strategy):
    METADATA = {
        "name": "StochasticScalpReversion",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 200.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 24
        self.os_thresh = 8.0
        self.ob_thresh = 92.0
        self.cooldown_bars = 96
        self.last_trade_bar = -200
        self.last_exit_bar = -200

    def _stochastic(self, highs, lows, closes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        highest = max(highs[-period:])
        lowest = min(lows[-period:])
        if highest == lowest:
            return 50.0
        return ((closes[-1] - lowest) / (highest - lowest)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        highs = ctx.highs(self.period + 2)
        lows = ctx.lows(self.period + 2)
        closes = ctx.closes(self.period + 2)

        if len(closes) < self.period + 2:
            return None

        stoch_now = self._stochastic(highs, lows, closes, self.period)
        stoch_prev = self._stochastic(highs[:-1], lows[:-1], closes[:-1], self.period)

        if stoch_now is None or stoch_prev is None:
            return None

        # Position exit logic: return to 50 midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and stoch_now >= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "stoch_midline_reached", "stoch": round(stoch_now, 2)}
                )
            elif pos_dir == "short" and stoch_now <= 50.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "stoch_midline_reached", "stoch": round(stoch_now, 2)}
                )
            return None

        # Hard cooldown enforcement after trades and exits
        bars_since_entry = ctx.bar_index - self.last_trade_bar
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_entry < self.cooldown_bars or bars_since_exit < self.cooldown_bars:
            return None

        # Long entry: deep oversold hook up crossing above threshold
        if stoch_prev <= self.os_thresh and stoch_now > 10.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_deep_oversold_reversal",
                    "stoch_now": round(stoch_now, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close
                }
            )

        # Short entry: deep overbought hook down crossing below threshold
        if stoch_prev >= self.ob_thresh and stoch_now < 90.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "stoch_deep_overbought_reversal",
                    "stoch_now": round(stoch_now, 2),
                    "stoch_prev": round(stoch_prev, 2),
                    "price": ctx.bar.close
                }
            )

        return None