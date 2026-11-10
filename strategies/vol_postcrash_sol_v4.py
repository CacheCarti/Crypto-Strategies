from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class VolatilityRecoveryStrategy(Strategy):
    METADATA = {
        "name": "VolatilityRecoverySOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,  # 2 hours average hold
        "warmup_bars": 50,
        "required_features": [], # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_flush = 5      # Lookback for sharp flush
        self.period_stabilization = 3 # Bars to confirm stabilization
        self.period_recovery_ema = 10 # EMA for recovery drift
        self.cooldown_bars = 12    # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Volatility thresholds (adjust based on SOL's typical movement)
        self.flush_threshold_pct = -1.0 # -1.0% drop in self.period_flush bars
        self.stabilization_range_pct = 0.5 # Price range within 0.5% during stabilization

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first 'period' values
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filter: only trade in calm or normal regimes
        if ctx.market.get("regime", "NORMAL") not in ["CALM", "NORMAL"]:
             return None

        # 1. Detect a sharp flush (significant drop over `period_flush` bars)
        flush_closes = closes[-(self.period_flush + 1):]
        if len(flush_closes) < self.period_flush + 1:
            return None

        flush_start_price = flush_closes[0]
        flush_end_price = flush_closes[-1]
        flush_pct_change = ((flush_end_price - flush_start_price) / flush_start_price) * 100

        is_flush = flush_pct_change < self.flush_threshold_pct

        if not is_flush:
            return None

        # 2. Check for stabilization after the flush
        # This requires looking at bars *after* the flush period.
        # We need at least self.period_stabilization bars *after* the flush period
        # to check for stabilization.
        stabilization_start_index = len(closes) - self.period_stabilization
        if stabilization_start_index < self.period_flush: # Not enough bars after flush
            return None

        stabilization_closes = closes[stabilization_start_index:]
        stabilization_highs = highs[stabilization_start_index:]
        stabilization_lows = lows[stabilization_start_index:]

        if len(stabilization_closes) < self.period_stabilization:
            return None

        stabilization_range = max(stabilization_highs) - min(stabilization_lows)
        stabilization_range_pct = (stabilization_range / stabilization_closes[0]) * 100

        is_stabilized = stabilization_range_pct < self.stabilization_range_pct

        if not is_stabilized:
            return None

        # 3. Recovery drift confirmation (price above short-term EMA)
        recovery_ema = self._ema(closes, self.period_recovery_ema)
        if recovery_ema is None:
            return None

        current_price = ctx.bar.close
        is_recovering = current_price > recovery_ema

        if not is_recovering:
            return None

        # If all conditions met, send a long signal
        if not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75, # High confidence for this specific setup
                metadata={
                    "reason": "volatility_recovery_long",
                    "flush_pct_change": round(flush_pct_change, 2),
                    "stabilization_range_pct": round(stabilization_range_pct, 2),
                    "current_price": current_price,
                    "recovery_ema": recovery_ema,
                    "market_regime": ctx.market.get("regime", "NORMAL"),
                }
            )
        elif ctx.position_direction() == "long":
            # Already long, hold position
            return ctx.signal("hold")

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close to allow cooldown to start
        pass # The cooldown is handled in on_bar using self.last_trade_bar