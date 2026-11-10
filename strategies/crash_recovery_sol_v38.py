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
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.panic_high = 0.0
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position Management
        if ctx.has_position():
            bars_held = current_bar - self.entry_bar
            flush_midpoint = (self.panic_high + self.panic_low) / 2.0 if self.panic_high > 0 else 0.0

            # Dynamic Target: Near flush midpoint or time-based exhaustion
            if flush_midpoint > 0 and current_close >= flush_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reached",
                        "close": current_close,
                        "flush_mid": flush_midpoint,
                        "bars_held": bars_held
                    }
                )

            # Max hold guard: exit after 18 bars (18 hours) to prevent stagnant capital
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_exhaustion",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            return None

        # Cooldown guard after recent exit
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in catastrophic meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Flush Detection: check drop over the last 24 bars
        window_high = max(highs[-self.lookback:])
        if window_high <= 0:
            return None

        drop_pct = (window_high - current_close) / window_high

        # Require a panic flush of at least 7.0%
        if drop_pct >= 0.07:
            # Stabilization signal: current close crosses above the previous bar's high
            prev_high = highs[-2]
            if current_close > prev_high:
                # RSI check to ensure we are entering deeply discounted conditions, not early knife-catching
                rsi = self._rsi(closes, 14)
                if rsi is not None and rsi < 42.0:
                    window_low = min(lows[-self.lookback:])
                    
                    # Stop-loss buffer below panic low
                    sl_dist = (current_close - window_low) / current_close
                    sl_bps = max(200.0, min(500.0, sl_dist * 10000.0 + 50.0))
                    
                    # Target halfway back to high
                    tp_dist = (window_high - current_close) * 0.5 / current_close
                    tp_bps = max(250.0, min(650.0, tp_dist * 10000.0))

                    # Scale confidence with panic depth
                    confidence = min(0.95, max(0.65, 0.65 + (drop_pct - 0.07) * 2.5))

                    self.panic_high = window_high
                    self.panic_low = window_low
                    self.entry_bar = current_bar

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "panic_recovery_reversal",
                            "drop_pct": round(drop_pct * 100, 2),
                            "rsi": round(rsi, 2),
                            "window_high": window_high,
                            "window_low": window_low,
                            "prev_high": prev_high,
                            "sl_bps": round(sl_bps, 1),
                            "tp_bps": round(tp_bps, 1)
                        }
                    )

        return None