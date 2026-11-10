from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target recovery window
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.05
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.target_midpoint = 0.0
        self.structure_stop = 0.0
        self.max_hold_bars = 16

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.target_midpoint = 0.0
        self.structure_stop = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 2:
            return None

        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Structural exit: Reached the 50% retracement of the flush
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "target_midpoint": self.target_midpoint,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            
            # Structure stop: Break below the flush low
            if self.structure_stop > 0 and current_close < self.structure_stop:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "flush_structure_stop_violated",
                        "structure_stop": self.structure_stop,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            # Time-based expiration exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_time_horizon_reached",
                        "bars_held": bars_held,
                        "close": current_close
                    }
                )
            return None

        # Check cooldown period after previous trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate flush characteristics over the lookback window
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0:
            return None

        # Condition 1: Drop magnitude >= 5% from recent peak
        drop_pct = (peak_high - trough_low) / peak_high
        if drop_pct < self.min_drop_pct:
            return None

        # Condition 2: Stabilization confirmation
        # Current bar closes above previous bar's high, confirming buying step-in
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Condition 3: Must be currently in the bottom half of the flush range to enter
        midpoint = (peak_high + trough_low) / 2.0
        if current_close >= midpoint:
            return None

        # Structural calculations for risk management
        risk_dist = current_close - trough_low
        if risk_dist <= 0:
            return None

        stop_bps = (risk_dist / current_close) * 10000.0
        # Bound stop loss between 150 bps and 450 bps
        stop_bps = max(150.0, min(stop_bps, 450.0))

        reward_dist = midpoint - current_close
        tp_bps = (reward_dist / current_close) * 10000.0
        tp_bps = max(200.0, min(tp_bps, 600.0))

        # Sentiment feature context
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        
        # Scaling confidence based on sentiment and severity of flush
        confidence = 0.65
        if fear_greed < 30.0:
            confidence += 0.15
        if drop_pct > 0.08:
            confidence += 0.10
        confidence = min(confidence, 0.95)

        # Store trade state
        self.entry_bar = ctx.bar_index
        self.flush_high = peak_high
        self.target_midpoint = midpoint
        self.structure_stop = trough_low

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "panic_flush_stabilization_entry",
                "drop_pct": round(drop_pct * 100, 2),
                "peak_high": peak_high,
                "trough_low": trough_low,
                "target_midpoint": round(midpoint, 2),
                "stop_bps": round(stop_bps, 1),
                "tp_bps": round(tp_bps, 1),
                "fear_greed": fear_greed
            }
        )