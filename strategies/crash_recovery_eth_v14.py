from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.05  # 5% flush
        self.cooldown_period = 8  # wait 8 bars after exit
        self.last_exit_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0
        self.entry_bar = -100
        self.max_hold_bars = 24

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Check ongoing position management for target exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_price = ctx.bar.close

            # Target exit near midpoint of the pre-flush range
            if self.target_midpoint > 0 and curr_price >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "price": round(curr_price, 2),
                        "bars_held": bars_held,
                    }
                )

            # Time-based stop to avoid dead capital in stagnant recoveries
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_recovery_hold_exceeded",
                        "price": round(curr_price, 2),
                        "bars_held": bars_held,
                    }
                )
            return None

        # Check mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Avoid entering during systemic meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Drop detection over the lookback window (24 bars)
        # Compare current/recent levels against highest high in window
        recent_high = max(highs[-(self.lookback + 1):-1])
        recent_low = min(lows[-(self.lookback + 1):])
        prev_high = highs[-2]
        curr_close = closes[-1]
        
        if recent_high <= 0:
            return None

        # Drawdown from recent peak
        max_drawdown = (recent_high - recent_low) / recent_high
        if max_drawdown < self.drop_thresh:
            return None

        # Stabilization confirmation:
        # Current bar must close above the previous bar's high, confirming a short-term structural shift/reversal
        stabilized = curr_close > prev_high

        # Avoid chasing if price has already retraced more than 60% of the flush
        flush_range = recent_high - recent_low
        midpoint = recent_low + (flush_range * 0.50)
        already_over_midpoint = curr_close >= midpoint

        if stabilized and not already_over_midpoint:
            self.flush_high = recent_high
            self.flush_low = recent_low
            self.target_midpoint = midpoint
            self.entry_bar = ctx.bar_index

            # Calculate dynamic stop-loss right below the flush low (with buffer), capped sensibly
            risk_dist = max(curr_close - recent_low, curr_close * 0.015)
            sl_bps = min(max((risk_dist / curr_close) * 10000.0, 150.0), 450.0)
            
            # Dynamic TP target to reach near the flush midpoint
            gain_target = max(midpoint - curr_close, curr_close * 0.02)
            tp_bps = min(max((gain_target / curr_close) * 10000.0, 200.0), 600.0)

            # Regime-sensitive confidence sizing
            fgi = ctx.features.get("fear_greed_index", 50)
            confidence = 0.75 if fgi < 35 else 0.65

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_reversal_stabilization",
                    "max_drawdown_pct": round(max_drawdown * 100, 2),
                    "recent_high": round(recent_high, 2),
                    "flush_low": round(recent_low, 2),
                    "target_midpoint": round(midpoint, 2),
                    "prev_high": round(prev_high, 2),
                    "close": round(curr_close, 2),
                    "fgi": fgi,
                }
            )

        return None