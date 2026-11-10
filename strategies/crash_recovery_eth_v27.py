from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.045         # 4.5% drop over 24h to capture genuine flush without being too rare
        self.cooldown_bars = 8           # Wait 8 bars (~8 hours) after exit
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_idx = ctx.bar_index

        # Position Management & Custom Exit Logic
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar if self.entry_bar > 0 else 1
            # Exit condition 1: reached midpoint target of the flush range
            if self.target_price > 0.0 and current_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_price": round(self.target_price, 2),
                        "close": current_close,
                        "bars_held": bars_held,
                    }
                )

            # Exit condition 2: time-decay stop after 14 bars if momentum stalls
            if bars_held >= 14:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_decay_reversion_exhausted",
                        "bars_held": bars_held,
                        "close": current_close,
                    }
                )
            return None

        # Mandatory Cooldown Gate
        if (current_idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check for Panic Flush over the last 24 bars
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        flush_magnitude = (recent_high - current_close) / recent_high

        if flush_magnitude < self.drop_thresh:
            return None

        # Stabilization Trigger: current bar closes above previous bar's high
        prev_high = highs[-2]
        stabilized = current_close > prev_high

        if not stabilized:
            return None

        # Secondary filter: RSI confirming oversold bounce (not already overbought)
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi > 52.0:
            return None

        # Calculate Flush Midpoint Target
        flush_midpoint = recent_low + (recent_high - recent_low) * 0.50
        upside_pot = (flush_midpoint - current_close) / current_close

        # Must have at least 100 bps upside to midpoint to justify round-trip friction
        if upside_pot < 0.010:
            return None

        # Confidence based on flush depth and oversold degree
        confidence = min(0.90, max(0.55, 0.5 + (flush_magnitude - self.drop_thresh) * 4.0))

        # Store entry state
        self.entry_bar = current_idx
        self.flush_high = recent_high
        self.flush_low = recent_low
        self.target_price = flush_midpoint

        return ctx.signal(
            "long",
            confidence=round(confidence, 2),
            stop_loss_bps=320.0,
            take_profit_bps=450.0,
            horizon_seconds=21600,
            metadata={
                "reason": "panic_flush_stabilization_bounce",
                "drop_pct": round(flush_magnitude * 100.0, 2),
                "rsi": round(rsi, 2),
                "midpoint_target": round(flush_midpoint, 2),
                "prev_high": round(prev_high, 2),
                "close": round(current_close, 2),
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999
        self.target_price = 0.0