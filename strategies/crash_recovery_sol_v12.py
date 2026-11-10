from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.max_hold_bars = 20
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_high = 0.0
        self.panic_low = 0.0
        self.flush_target = 0.0

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
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 26:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target exit: flush-range midpoint
            if current_close >= self.flush_target and self.flush_target > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "target": self.flush_target,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            # Timed exit: volatility flush recoveries should be swift
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_timeout",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid outright crisis liquidation spirals
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Compute 24-bar rolling flush
        lookback = 24
        window_highs = highs[-lookback:]
        window_lows = lows[-lookback:]
        p_high = max(window_highs)
        p_low = min(window_lows)
        current_close = closes[-1]
        prev_high = highs[-2]

        drop_pct = (current_close - p_high) / p_high

        # Trigger conditions:
        # 1. 24-bar drop of at least 7%
        # 2. Stabilization bar: current bar closes above previous bar's high
        # 3. RSI confirms washout status (RSI < 42)
        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        if drop_pct <= -0.07 and current_close > prev_high and rsi < 42:
            self.panic_high = p_high
            self.panic_low = p_low
            # Structural target: midpoint between panic low and panic high
            self.flush_target = (p_high + p_low) / 2.0
            self.entry_bar = ctx.bar_index

            # Dynamic stop: placed below the structural panic low with a buffer
            sl_distance = (current_close - p_low) / current_close
            sl_bps = max(250.0, min(650.0, sl_distance * 10000.0 + 50.0))

            tp_distance = (self.flush_target - current_close) / current_close
            tp_bps = max(200.0, min(750.0, tp_distance * 10000.0))

            fear_greed = ctx.features.get("fear_greed_index", 50)
            conf = 0.75 if fear_greed < 35 else 0.60

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=36000,
                metadata={
                    "reason": "sol_flush_reversal_stabilization",
                    "drop_pct": drop_pct,
                    "rsi": rsi,
                    "panic_low": p_low,
                    "panic_high": p_high,
                    "flush_target": self.flush_target,
                    "sl_bps": sl_bps,
                    "tp_bps": tp_bps,
                    "fear_greed": fear_greed
                }
            )

        return None