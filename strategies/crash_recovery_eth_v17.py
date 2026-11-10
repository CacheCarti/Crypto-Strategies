from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.recovery_target = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 26:
            return None

        current_close = ctx.bar.close
        prev_high = highs[-2]
        prev_low = lows[-2]

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint target exit or time-decay exit
            reached_target = self.recovery_target > 0 and current_close >= self.recovery_target
            max_hold_reached = bars_held >= 12  # 12 hours max hold to avoid dragging in bear regimes

            if reached_target or max_hold_reached:
                reason = "hit_flush_midpoint" if reached_target else "max_hold_time_elapsed"
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "close": current_close,
                        "target": self.recovery_target,
                        "bars_held": bars_held
                    }
                )
            return None

        # Check cooldown to prevent clustering
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crash / Flush condition: >= 4.5% drop over the last 24 bars (~24h)
        lookback_bars = 24
        window_high = max(highs[-lookback_bars:])
        window_low = min(lows[-lookback_bars:])
        
        if window_high <= 0:
            return None

        drawdown_pct = (window_high - current_close) / window_high

        # Selective threshold: drop must be at least 4.5% to capture genuine flushes
        is_flush = drawdown_pct >= 0.045

        # Stabilization trigger: current close reclaims previous bar high, showing buyers stepping in
        reclaimed_prev_high = current_close > prev_high
        
        # Additional filter: RSI oversold or rebounding from deep oversold (< 42)
        rsi = self._rsi(closes, period=14)

        # Skip extreme crisis meltdowns where orderbooks evaporate
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        if is_flush and reclaimed_prev_high and rsi < 45.0:
            self.entry_bar = ctx.bar_index
            self.flush_high = window_high
            self.flush_low = min(window_low, ctx.bar.low)
            
            # Midpoint of the flush range as primary bounce target
            self.recovery_target = (self.flush_high + self.flush_low) * 0.50
            
            # Scaled confidence based on flush depth
            confidence = min(0.90, max(0.60, 0.60 + (drawdown_pct - 0.045) * 5.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=300.0,
                take_profit_bps=420.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "panic_flush_stabilization_reclaim",
                    "drawdown_pct": round(drawdown_pct, 4),
                    "rsi": round(rsi, 2),
                    "flush_high": self.flush_high,
                    "flush_low": self.flush_low,
                    "recovery_target": round(self.recovery_target, 2),
                    "regime": regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.recovery_target = 0.0