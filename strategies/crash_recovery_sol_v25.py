from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "SolPanicFlushRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.07  # 7% drop over 24 bars
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.target_price = 0.0
        self.stop_price = 0.0
        self.panic_low = 0.0

    def _rsi(self, closes, period=14):
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
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.stop_price = 0.0
        self.panic_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Check existing position management
        if ctx.has_position():
            curr_close = ctx.bar.close
            # Explicit exit near range midpoint or below panic low stop
            if self.target_price > 0.0 and curr_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "close": curr_close,
                        "target_price": self.target_price,
                        "panic_low": self.panic_low,
                    },
                )
            if self.stop_price > 0.0 and curr_close <= self.stop_price:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "panic_low_stop_violated",
                        "close": curr_close,
                        "stop_price": self.stop_price,
                        "panic_low": self.panic_low,
                    },
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = closes[-1]
        prev_high = highs[-2]
        
        # Lookback flush calculation (peak high in window vs recent low)
        lookback_high = max(highs[-(self.lookback + 1):-1])
        lookback_low = min(lows[-(self.lookback + 1):-1])
        
        # Calculate flush magnitude
        flush_drop = (lookback_high - lookback_low) / lookback_high

        # Event boundary:
        # 1. Significant drop over the window (>= 7%)
        # 2. Reversal confirmation: bar closes above previous bar's high
        # 3. Clean stabilization: RSI showing deep oversold recovery (RSI <= 42)
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi > 42.0:
            return None

        if flush_drop >= self.drop_threshold and current_close > prev_high:
            midpoint = (lookback_high + lookback_low) / 2.0
            
            # Require meaningful upside room to midpoint (at least 2.5%)
            if midpoint <= current_close * 1.025:
                return None

            self.panic_low = lookback_low
            self.stop_price = lookback_low * 0.992  # Just below panic low
            self.target_price = midpoint

            sl_bps = max(180.0, min(500.0, ((current_close - self.stop_price) / current_close) * 10000.0))
            tp_bps = max(300.0, min(800.0, ((self.target_price - current_close) / current_close) * 10000.0))

            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "sol_panic_reversal_stabilization",
                    "flush_drop_pct": round(flush_drop * 100, 2),
                    "lookback_high": round(lookback_high, 2),
                    "panic_low": round(lookback_low, 2),
                    "midpoint_target": round(midpoint, 2),
                    "rsi": round(rsi, 2),
                    "close": round(current_close, 2),
                    "prev_high": round(prev_high, 2),
                },
            )

        return None