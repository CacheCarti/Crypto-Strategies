from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0
        self.stop_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.stop_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close

        # Manage existing long position
        if ctx.has_position():
            # Check midpoint target or trailing stop logic
            if self.target_price > 0 and current_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "target": self.target_price, "close": current_close}
                )
            if self.stop_price > 0 and current_close <= self.stop_price:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "panic_low_violated", "stop": self.stop_price, "close": current_close}
                )
            return None

        # Check cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush calculation: Rolling drop over the last 24 bars
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        
        if recent_high <= 0:
            return None

        total_drop_pct = (recent_high - current_close) / recent_high

        # Relative volatility to prevent overtrading during low-vol churn
        atr = self._atr(highs, lows, closes, period=14)
        atr_pct = (atr / current_close) if current_close > 0 else 0.0
        
        # Adaptive threshold: Drop must exceed at least 7% AND at least 2.5x the hourly ATR percentage
        is_severe_flush = (total_drop_pct >= 0.07) and (total_drop_pct >= (atr_pct * 2.5))

        # Reversal stabilization trigger: Bar closes above previous bar's high
        prev_high = highs[-2]
        is_stabilization = (current_close > prev_high) and (closes[-2] <= prev_high)

        # Ensure we are not already deep in a meltdown regime
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        if is_severe_flush and is_stabilization and (crisis_score < 0.85) and (regime != "MELTDOWN"):
            # Flush midpoint target
            flush_range = recent_high - recent_low
            self.target_price = recent_low + (flush_range * 0.50)
            self.stop_price = recent_low * 0.995

            # Calculate BPS for dynamic SL and TP
            sl_bps = max(150.0, min(600.0, ((current_close - self.stop_price) / current_close) * 10000))
            tp_bps = max(200.0, min(800.0, ((self.target_price - current_close) / current_close) * 10000))

            # Sizing confidence scales with severity of flush (higher flush -> stronger bounce conviction)
            confidence = min(0.9, 0.65 + (total_drop_pct - 0.07) * 2.0)

            self.entry_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_panic_stabilization_reversal",
                    "total_drop_pct": round(total_drop_pct, 4),
                    "flush_range": round(flush_range, 2),
                    "target_price": round(self.target_price, 2),
                    "stop_price": round(self.stop_price, 2),
                    "atr": round(atr, 2),
                }
            )

        return None