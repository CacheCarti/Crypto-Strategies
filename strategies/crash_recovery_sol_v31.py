from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 43200,  # ~12 hours average swing hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.drop_thresh_pct = 0.07       # 7% dump in 24 bars
        self.cooldown_period = 16         # bars after exit before another trade
        self.last_exit_bar = -100
        self.panic_low = 0.0
        self.panic_high = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

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
        closes = ctx.closes(self.lookback_bars + 5)
        highs = ctx.highs(self.lookback_bars + 5)
        lows = ctx.lows(self.lookback_bars + 5)

        if len(closes) < self.lookback_bars + 2:
            return None

        # Position management
        if ctx.has_position():
            # Midpoint take profit or exit if momentum stalls
            current_close = ctx.bar.close
            entry_price = ctx.position.get("entry_price", current_close) if ctx.position else current_close
            target_midpoint = (self.panic_low + self.panic_high) / 2.0 if self.panic_high > self.panic_low else 0.0

            # Exit if recovered past flush range midpoint or hit 550 bps
            if target_midpoint > entry_price and current_close >= target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_reached",
                        "close": current_close,
                        "midpoint": target_midpoint,
                        "panic_low": self.panic_low,
                    }
                )

            # Trailing stop protection: exit if breaks below panic low
            if self.panic_low > 0 and current_close < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "panic_low_violation_stop",
                        "close": current_close,
                        "panic_low": self.panic_low,
                    }
                )
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # 1. Measure drawdown over the last 24 bars
        window_high = max(highs[-self.lookback_bars:])
        window_low = min(lows[-self.lookback_bars:])
        current_close = ctx.bar.close
        prev_close = closes[-2]
        prev_high = highs[-2]

        total_drop = (window_high - window_low) / window_high if window_high > 0 else 0.0
        drop_from_high = (window_high - current_close) / window_high if window_high > 0 else 0.0

        # Must have had a severe selloff (>= 7%) and still be depressed (at least 4.5% below high)
        if total_drop < self.drop_thresh_pct or drop_from_high < 0.045:
            return None

        # 2. Check for stabilization bar: current bar closes ABOVE previous bar's high
        stabilization_breakout = current_close > prev_high and current_close > prev_close

        if not stabilization_breakout:
            return None

        # 3. Confirmation indicators: RSI not overbought, market not in absolute meltdown
        rsi = self._rsi(closes, period=14)
        if rsi is None or rsi > 58.0:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Store panic boundaries for dynamic midpoint target & stop
        self.panic_high = window_high
        self.panic_low = window_low

        # Dynamic stop distance based on distance to panic low
        dist_to_low_bps = max(((current_close - self.panic_low) / current_close) * 10000.0, 150.0)
        sl_bps = min(max(dist_to_low_bps + 40.0, 200.0), 450.0)

        # Target midpoint of flush
        midpoint = (window_high + window_low) / 2.0
        tp_bps = 550.0
        if midpoint > current_close:
            mid_bps = ((midpoint - current_close) / current_close) * 10000.0
            tp_bps = min(max(mid_bps, 250.0), 650.0)

        confidence = 0.75 if rsi < 38.0 else 0.65

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_panic_stabilization_bar_cross",
                "drop_pct": round(total_drop * 100, 2),
                "rsi": round(rsi, 2),
                "panic_low": round(self.panic_low, 2),
                "panic_high": round(self.panic_high, 2),
                "close": round(current_close, 2),
                "prev_high": round(prev_high, 2),
            }
        )