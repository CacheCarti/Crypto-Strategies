from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.045
        self.cooldown_bars = 12
        self.max_hold_bars = 36
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.target_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        self.entry_bar = -1
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            current_close = ctx.bar.close

            # Mean-reversion exit target: flush midpoint reached
            if self.target_price > 0.0 and current_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "target": self.target_price, "price": current_close}
                )

            # Time-stop to avoid prolonged capital lockup in failed bounces
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_held, "price": current_close}
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip meltdown / systemic crisis environments
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Detect flush event: 24-bar cumulative drop
        recent_closes = closes[-(self.lookback + 1):]
        high_water = max(recent_closes[:-1])
        trough = min(recent_closes)
        drawdown = (trough - high_water) / high_water

        if drawdown > self.drop_thresh:
            return None

        # Stabilization confirmation: current close breaks above previous bar high
        prev_high = highs[-2]
        curr_close = ctx.bar.close
        if curr_close <= prev_high:
            return None

        # Avoid chasing if recovery has already retraced the majority of the move
        midpoint = trough + (high_water - trough) * 0.50
        if curr_close >= midpoint:
            return None

        rsi_val = self._rsi(closes, period=14)
        if rsi_val > 55.0:
            return None

        # Dynamic confidence based on severe oversold conditions
        conf = 0.70
        if rsi_val < 32.0:
            conf = 0.85
        elif rsi_val < 40.0:
            conf = 0.75

        self.entry_bar = ctx.bar_index
        self.target_price = midpoint

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "panic_flush_reversal_stabilization",
                "drawdown_pct": round(drawdown * 100, 2),
                "rsi": round(rsi_val, 2),
                "target_midpoint": round(midpoint, 2),
                "price": curr_close
            }
        )