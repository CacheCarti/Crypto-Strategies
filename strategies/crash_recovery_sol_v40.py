from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "SolPanicFlushRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.07          # 7%+ decline over lookback
        self.rsi_period = 14
        self.rsi_max = 38.0             # ensure genuine flush/exhaustion
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.panic_low = None
        self.flush_high = None

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
        self.panic_low = None
        self.flush_high = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Check existing position management
        if ctx.has_position():
            curr_close = closes[-1]
            # Exit near the midpoint of the flush range if defined
            if self.flush_high is not None and self.panic_low is not None:
                midpoint = (self.flush_high + self.panic_low) / 2.0
                if curr_close >= midpoint:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_range_midpoint_reached",
                            "midpoint": midpoint,
                            "close": curr_close,
                            "panic_low": self.panic_low,
                            "flush_high": self.flush_high
                        }
                    )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market breakdown guard (avoid absolute meltdown contagion)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Measure 24-bar drop
        rolling_high = max(highs[-self.lookback - 1:-1])
        recent_low = min(lows[-self.lookback:])
        current_close = closes[-1]
        prior_close = closes[-2]
        prior_high = highs[-2]

        total_drop = (rolling_high - recent_low) / rolling_high if rolling_high > 0 else 0.0

        if total_drop < self.drop_thresh:
            return None

        # RSI confirmation: oversold exhaustion
        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None or rsi_val > self.rsi_max:
            return None

        # Stabilization bar: current bar must close firmly above previous bar's high
        if current_close <= prior_high:
            return None

        # Extra sanity: must have closed green
        if current_close <= ctx.bar.open:
            return None

        # Record levels
        self.flush_high = rolling_high
        self.panic_low = recent_low

        # Calculate dynamic stop loss to panic low with buffer
        dist_to_low = current_close - self.panic_low
        sl_bps = max(250.0, min(500.0, (dist_to_low / current_close) * 10000.0 + 30.0))
        
        # Calculate dynamic TP targeting the range midpoint
        midpoint = (self.flush_high + self.panic_low) / 2.0
        tp_bps = max(350.0, min(800.0, ((midpoint - current_close) / current_close) * 10000.0))

        confidence = 0.70
        if rsi_val < 28.0:
            confidence += 0.15

        return ctx.signal(
            "long",
            confidence=min(0.95, confidence),
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_panic_flush_reversal_close_above_prior_high",
                "drop_pct": round(total_drop * 100.0, 2),
                "rsi": round(rsi_val, 2),
                "prior_high": prior_high,
                "panic_low": self.panic_low,
                "flush_high": self.flush_high,
                "midpoint_target": midpoint
            }
        )