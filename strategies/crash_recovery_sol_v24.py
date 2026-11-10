from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(self.lookback + 20)
        lows = ctx.lows(self.lookback + 20)

        if len(closes) < self.lookback + 14:
            return None

        # Manage open position
        if ctx.has_position():
            curr_c = ctx.bar.close
            # Check dynamic target: flush-range midpoint
            if self.tp_price > 0 and curr_c >= self.tp_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_reached",
                    "close": curr_c,
                    "target": self.tp_price,
                    "entry_bar": self.entry_bar
                })
            # Time-based max hold exit (24 bars ~ 24h)
            if ctx.bar_index - self.entry_bar >= 24:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_timeout",
                    "bars_held": ctx.bar_index - self.entry_bar,
                    "close": curr_c
                })
            return None

        # Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme system meltdown crisis where knives continue falling
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Compute dynamic flush: rolling 24-bar drop
        # Instead of fixed magic 7%, we check a drop > 6.0% or 3x local ATR drop
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        
        peak_idx = 0
        peak_val = window_highs[0]
        for i, h in enumerate(window_highs):
            if h > peak_val:
                peak_val = h
                peak_idx = i

        trough_val = min(window_lows[peak_idx:]) if peak_idx < len(window_lows) else min(window_lows)
        
        # Panic flush calculation from peak to trough
        flush_drop_pct = (peak_val - trough_val) / peak_val if peak_val > 0 else 0.0
        atr = self._atr(highs, lows, closes, 14) or (ctx.bar.close * 0.02)
        
        # Qualification: At least 6.5% drop from rolling peak in last 24 bars
        is_flush = flush_drop_pct >= 0.065 and (peak_val - trough_val) >= 2.5 * atr

        if not is_flush:
            return None

        # Stabilization signal: current bar closes above previous bar's high
        prev_high = highs[-2]
        curr_close = ctx.bar.close
        curr_low = ctx.bar.low

        stabilization = curr_close > prev_high and closes[-2] <= prev_high

        if stabilization:
            # Entry confirmed
            self.panic_low = trough_val
            self.panic_high = peak_val
            midpoint = (self.panic_high + self.panic_low) / 2.0
            self.tp_price = midpoint
            self.entry_bar = ctx.bar_index

            # Dynamic SL based on panic low minus buffer
            sl_bps = max(180.0, min(500.0, ((curr_close - self.panic_low) / curr_close) * 10000.0 + 40.0))
            tp_bps = max(250.0, min(650.0, ((midpoint - curr_close) / curr_close) * 10000.0))

            # Base confidence scaled by how deep the flush was
            confidence = min(0.95, max(0.55, 0.5 + flush_drop_pct * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "sol_panic_flush_reversal",
                    "flush_drop_pct": round(flush_drop_pct * 100, 2),
                    "panic_high": round(peak_val, 2),
                    "panic_low": round(trough_val, 2),
                    "midpoint_target": round(midpoint, 2),
                    "atr": round(atr, 2),
                    "close": curr_close
                }
            )

        return None