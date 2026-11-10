from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRecoveryDrift(Strategy):
    METADATA = {
        "name": "Volatility Recovery Drift SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Longer hold for recovery drift
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this pure price action strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 10  # Bars to look back for a sharp flush
        self.stabilization_period = 5 # Bars to wait for stabilization after flush
        self.entry_drift_period = 3 # Bars to check for upward drift after stabilization
        self.entry_threshold_multiplier = 1.5 # How many ATRs for a flush
        self.stabilization_std_multiplier = 0.5 # Max stddev for stabilization
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.has_position():
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- 1. Identify a sharp flush (significant drop) ---
        # Calculate ATR for dynamic threshold
        current_atr = self._atr(highs, lows, closes, self.flush_period)
        if current_atr is None or current_atr == 0:
            return None

        flush_threshold = self.entry_threshold_multiplier * current_atr
        
        # Check for a sharp drop in the last `flush_period` bars
        # A flush is defined as a significant drop from a recent high.
        # We look for a bar where the close is significantly lower than the open,
        # or a large body candle to the downside.
        
        # Check for a large red candle or significant drop from open to close
        flush_detected = False
        flush_bar_index = -1
        for i in range(1, self.flush_period + 1):
            if closes[-i] < opens[-i]: # Red candle
                drop = opens[-i] - closes[-i]
                if drop > flush_threshold:
                    flush_detected = True
                    flush_bar_index = len(closes) - i
                    break
            # Alternative: check if current close is significantly below highest high in period
            # This captures a wider range of flush scenarios
            if i > 1: # need at least 2 bars
                recent_high = max(highs[-(i):-1]) # Highs before the current potential flush bar
                if closes[-i] < recent_high - flush_threshold:
                    flush_detected = True
                    flush_bar_index = len(closes) - i
                    break
        
        if not flush_detected:
            return None

        # --- 2. Wait for stabilization after the flush ---
        # Stabilization period starts AFTER the flush bar
        stabilization_start_idx = flush_bar_index + 1
        stabilization_end_idx = stabilization_start_idx + self.stabilization_period

        if stabilization_end_idx >= len(closes):
            return None # Not enough bars for stabilization

        stabilization_closes = closes[stabilization_start_idx:stabilization_end_idx]
        if len(stabilization_closes) < self.stabilization_period:
            return None

        stabilization_std = self._stddev(stabilization_closes, self.stabilization_period)
        if stabilization_std is None:
            return None

        # Compare stabilization std with a fraction of current ATR
        if stabilization_std > (current_atr * self.stabilization_std_multiplier):
            return None # Not stable enough

        # --- 3. Check for upward drift during the stabilization period ---
        # The closing price at the end of stabilization should be higher than the closing price at the beginning
        if stabilization_closes[-1] <= stabilization_closes[0]:
            return None # No upward drift during stabilization

        # --- Entry Signal ---
        # All conditions met: flush, stabilization, and slight upward drift
        self.last_trade_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=0.75, # High confidence for this specific setup
            metadata={
                "reason": "volatility_flush_stabilization_recovery",
                "flush_bar_close": closes[flush_bar_index],
                "flush_threshold_atr": flush_threshold,
                "stabilization_std": stabilization_std,
                "stabilization_end_price": stabilization_closes[-1],
                "stabilization_start_price": stabilization_closes[0],
                "current_price": ctx.bar.close,
                "current_atr": current_atr,
                "regime": ctx.regime,
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive logic
        # For simplicity, no adaptive logic in this version.
        pass