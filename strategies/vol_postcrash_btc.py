from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityDriftRecovery(Strategy):
    METADATA = {
        "name": "Volatility Drift Recovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.vol_period = 20 # for ATR
        self.flush_threshold_atr_multi = 2.0 # how many ATRs for a "flush"
        self.stabilization_bars = 3 # how many bars after flush to look for stabilization
        self.recovery_drift_bars = 5 # how many bars for the recovery drift
        self.cooldown_bars = 10 # bars to wait after a position closes
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # State variables for detecting flush and stabilization
        self.in_flush_state = False
        self.flush_start_bar = -1
        self.flush_low_price = math.inf
        self.flush_high_price = -math.inf

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.vol_period)
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if atr is None or fast_ema is None or slow_ema is None:
            return None

        # Condition 1: Detect a sharp flush (significant drop within a bar or two)
        # A flush is defined as a large candle downwards, exceeding a multiple of ATR
        flush_detected = False
        if current_open > current_close: # Red candle
            drop_size = current_open - current_close
            if drop_size > self.flush_threshold_atr_multi * atr:
                flush_detected = True

        if not self.in_flush_state and flush_detected:
            self.in_flush_state = True
            self.flush_start_bar = ctx.bar_index
            self.flush_low_price = current_close
            self.flush_high_price = current_open # Capture the high before the drop
            return None # Don't trade on the flush bar itself

        # If in flush state, update flush range
        if self.in_flush_state:
            self.flush_low_price = min(self.flush_low_price, ctx.bar.low)
            self.flush_high_price = max(self.flush_high_price, ctx.bar.high)

            # Condition 2: Wait for stabilization after the flush
            bars_since_flush = ctx.bar_index - self.flush_start_bar
            if bars_since_flush >= self.stabilization_bars:
                # Check for stabilization: price not making new lows, possibly tight range
                # We consider it stabilized if the current close is above the flush low
                # and not significantly lower than previous bars' lows within the stabilization period
                stabilized = current_close > self.flush_low_price # Basic stabilization check

                # Further stabilization check: price not dropping further during stabilization bars
                # Look back 'stabilization_bars' from current
                recent_lows = lows[-(self.stabilization_bars+1):-1] # Exclude current bar low
                if recent_lows and current_close < min(recent_lows):
                    stabilized = False # If we're making new lows, not stabilized

                if stabilized:
                    # Condition 3: Look for recovery drift (fast EMA crossing above slow EMA)
                    # We need to ensure the cross happens after stabilization
                    if fast_ema > slow_ema and closes[-2] < slow_ema: # Fast EMA just crossed above slow EMA
                        # Additional check: current price is still relatively low compared to flush high
                        # to ensure we are buying a dip, not after a full recovery
                        if current_close < self.flush_high_price * 0.98: # Still within 2% of flush high
                            if not ctx.has_position():
                                self.in_flush_state = False # Reset state after signal
                                self.last_trade_bar = ctx.bar_index
                                return ctx.signal(
                                    "long",
                                    confidence=0.75,
                                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                                    take_profit_bps=self.METADATA["declared_tp_bps"],
                                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                                    metadata={
                                        "reason": "volatility_drift_recovery_long",
                                        "current_close": current_close,
                                        "fast_ema": fast_ema,
                                        "slow_ema": slow_ema,
                                        "atr": atr,
                                        "flush_start_bar": self.flush_start_bar,
                                        "flush_low_price": self.flush_low_price,
                                        "bars_since_flush": bars_since_flush,
                                    }
                                )
                else: # If not stabilized after stabilization bars, reset flush state
                    self.in_flush_state = False

            # If we've been in a flush state too long without stabilization/recovery, reset
            if bars_since_flush > (self.stabilization_bars + self.recovery_drift_bars + 5): # Give it some extra bars
                self.in_flush_state = False

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, if needed
        # self.last_trade_bar = ctx.bar_index # Cooldown is already handled in on_bar
        pass