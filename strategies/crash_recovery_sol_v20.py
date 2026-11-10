from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.07  # 7% drop over 24 bars
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.entry_bar = -100

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
        self.panic_low = 0.0
        self.panic_high = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position management
        if ctx.has_position():
            # If position is held, check if target (range midpoint) reached or time exit
            bars_held = current_bar - self.entry_bar
            
            # Midpoint target exit
            midpoint = (self.panic_high + self.panic_low) / 2.0
            if self.panic_high > 0 and current_close >= midpoint and midpoint > self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "current_close": current_close,
                        "midpoint": midpoint,
                        "panic_low": self.panic_low,
                        "panic_high": self.panic_high,
                        "bars_held": bars_held
                    }
                )

            # Max hold duration emergency flatten (after ~18 hours)
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_reversion_failed", "bars_held": bars_held, "price": current_close}
                )

            return None

        # Cooldown guard after recent exit
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in catastrophic meltdown regime
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Calculate 24-bar drop
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        
        if window_high <= 0:
            return None

        drop_pct = (window_high - current_close) / window_high

        # Primary Condition 1: Substantial flush (>7%) over the last 24 bars
        if drop_pct < self.min_drop_pct:
            return None

        # Primary Condition 2: Stabilization reversal candle
        # Bar closes strictly above the prior bar's high, confirming a short-term bottom
        prev_high = highs[-2]
        is_stabilizing = current_close > prev_high

        if not is_stabilizing:
            return None

        # Primary Condition 3: Deeply oversold momentum confirming exhaustion (RSI < 38)
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 38.0:
            return None

        # Measure dynamic SL & TP based on panic geometry
        local_panic_low = min(lows[-6:])
        sl_bps = max(150.0, min(500.0, ((current_close - local_panic_low) / current_close) * 10000.0 + 30.0))
        
        midpoint = (window_high + local_panic_low) / 2.0
        raw_tp_bps = ((midpoint - current_close) / current_close) * 10000.0
        tp_bps = max(250.0, min(700.0, raw_tp_bps))

        # Ensure favorable risk-to-reward
        if tp_bps < sl_bps:
            return None

        # Save state for position management
        self.panic_low = local_panic_low
        self.panic_high = window_high
        self.entry_bar = current_bar

        confidence = min(0.95, max(0.65, drop_pct * 5.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=21600,
            metadata={
                "reason": "sol_panic_rebound_stabilization",
                "drop_pct": drop_pct,
                "rsi": rsi_val,
                "prev_high": prev_high,
                "panic_low": local_panic_low,
                "panic_high": window_high,
                "sl_bps": sl_bps,
                "tp_bps": tp_bps
            }
        )