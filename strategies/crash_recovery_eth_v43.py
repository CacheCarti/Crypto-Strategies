from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target recovery horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold_pct = -0.045  # ~4.5% to 5.0% flush threshold
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0
        self.structural_stop = 0.0
        self.max_hold_bars = 18

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
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

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target reached: exit near midpoint of the flush
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "price": current_close,
                    "target_midpoint": self.target_midpoint,
                    "bars_held": bars_held
                })

            # Structural breakdown stop: lowest low of flush violated
            if self.structural_stop > 0 and current_close < self.structural_stop:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "structural_support_broken",
                    "price": current_close,
                    "structural_stop": self.structural_stop,
                    "bars_held": bars_held
                })

            # Time-based expiration exit: avoid capital drag if recovery fails to materialize promptly
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "timed_recovery_exit",
                    "price": current_close,
                    "bars_held": bars_held
                })

            return None

        # Enforce post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: Skip extreme structural market meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Calculate 24-bar return
        ref_price = closes[-25]
        current_price = closes[-1]
        flush_return = (current_price - ref_price) / ref_price

        # Must have experienced a severe drop over the last 24 bars
        if flush_return > self.drop_threshold_pct:
            return None

        # Stabilization condition: current bar closes above previous bar's high
        prev_high = highs[-2]
        if current_price <= prev_high:
            return None

        # Compute flush range anchor points
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        swing_high = max(window_highs)
        swing_low = min(window_lows)
        midpoint = (swing_high + swing_low) / 2.0

        # Require reasonable distance to midpoint to justify round-trip friction
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or (midpoint - current_price) < 0.8 * atr:
            return None

        # Calculate dynamic SL and TP in basis points
        risk_dist = current_price - (swing_low - 0.25 * atr)
        if risk_dist <= 0:
            return None

        sl_bps = min(max((risk_dist / current_price) * 10000.0, 180.0), 450.0)
        tp_dist = midpoint - current_price
        tp_bps = min(max((tp_dist / current_price) * 10000.0, 220.0), 600.0)

        self.target_midpoint = midpoint
        self.structural_stop = swing_low - 0.25 * atr
        self.entry_bar = ctx.bar_index

        confidence = min(0.5 + abs(flush_return) * 3.0, 0.95)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.max_hold_bars * 3600,
            metadata={
                "reason": "flush_stabilization_long",
                "flush_return_pct": round(flush_return * 100, 2),
                "swing_high": swing_high,
                "swing_low": swing_low,
                "midpoint": midpoint,
                "structural_stop": self.structural_stop,
                "atr": atr,
                "price": current_price
            }
        )