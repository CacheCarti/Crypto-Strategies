from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.05
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0
        self.structural_stop = 0.0
        self.max_hold_bars = 16

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
        self.target_midpoint = 0.0
        self.structural_stop = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target reached (midpoint of flush)
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "close": current_close, "target": self.target_midpoint}
                )

            # Structural swing low violated
            if self.structural_stop > 0 and current_close < self.structural_stop:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "structural_stop_breached", "close": current_close, "stop": self.structural_stop}
                )

            # Timed exit: panic recoveries are quick mean-reversions; if it stagnates, exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "timed_recovery_exhaustion", "bars_held": bars_held}
                )

            return None

        # Check cooldown to avoid churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate multi-bar flush over past 24 bars
        anchor_close = closes[- (self.lookback + 1)]
        current_close = closes[-1]
        prior_close = closes[-2]
        prior_high = highs[-2]

        drop_pct = (current_close - anchor_close) / anchor_close

        # Stabilization trigger: closed above previous bar's high after a >=5% flush
        stabilized = (current_close > prior_high) and (current_close > prior_close)

        if drop_pct <= self.flush_threshold_pct and stabilized:
            flush_high = max(highs[-(self.lookback + 1):])
            flush_low = min(lows[-(self.lookback + 1):])

            # Calculate mid-point of the flush range as recovery target
            midpoint = (flush_high + flush_low) / 2.0

            # Don't buy if already beyond the midpoint
            if current_close >= midpoint:
                return None

            # ATR for buffer
            atr = self._atr(highs, lows, closes, 14)
            if atr == 0.0:
                atr = current_close * 0.01

            # Volatility-anchored structural stop just below flush low
            struct_stop = flush_low - (0.5 * atr)
            stop_dist = current_close - struct_stop
            if stop_dist <= 0:
                return None

            sl_bps = min(max((stop_dist / current_close) * 10000.0, 200.0), 600.0)
            tp_bps = min(max(((midpoint - current_close) / current_close) * 10000.0, 250.0), 800.0)

            self.target_midpoint = midpoint
            self.structural_stop = struct_stop
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, 0.6 + abs(drop_pct) * 2.0)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_drop_stabilization",
                    "drop_pct": drop_pct,
                    "flush_high": flush_high,
                    "flush_low": flush_low,
                    "target_midpoint": midpoint,
                    "structural_stop": struct_stop,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps
                }
            )

        return None