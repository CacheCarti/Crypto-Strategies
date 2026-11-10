from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.05       # 5% flush
        self.cooldown_bars = 8           # Bars to wait after exit or signal
        self.last_trade_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.entry_price = 0.0
        self.target_price = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Check existing position for take-profit midpoint reversion or timeout
        if ctx.has_position():
            if ctx.position_direction() == "long":
                current_price = ctx.bar.close
                # If target price set, exit near the flush midpoint
                if self.target_price > 0 and current_price >= self.target_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_reversion_target_hit",
                            "target_price": round(self.target_price, 2),
                            "current_price": round(current_price, 2),
                            "entry_price": round(self.entry_price, 2),
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdown / complete chaos where dip-buying fails
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Measure drop over 24 bars
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        current_close = closes[-1]
        prev_high = highs[-2]
        prev_close = closes[-2]

        if recent_high <= 0:
            return None

        drawdown = (recent_high - recent_low) / recent_high

        # Strict event boundary:
        # 1. 24-bar drop exceeds threshold (5%)
        # 2. Previous bar was deeply depressed (low near the recent low)
        # 3. Stabilization trigger: current close breaks cleanly above previous bar's high
        # 4. Current bar is bullish (close > open)
        if drawdown >= self.drop_threshold:
            stabilized = (current_close > prev_high) and (ctx.bar.close > ctx.bar.open)
            near_trough = (prev_close - recent_low) / (recent_high - recent_low + 1e-6) < 0.35

            if stabilized and near_trough:
                # Dynamic targets based on flush range
                flush_midpoint = (recent_high + recent_low) / 2.0
                dist_to_mid_bps = ((flush_midpoint - current_close) / current_close) * 10000.0

                # Only take the trade if there's sufficient room to the midpoint (> 100 bps)
                if dist_to_mid_bps >= 120.0:
                    self.entry_price = current_close
                    self.flush_high = recent_high
                    self.flush_low = recent_low
                    self.target_price = flush_midpoint
                    self.last_trade_bar = ctx.bar_index

                    # Set adaptive SL below the flush low or default SL bps
                    dist_to_low_bps = ((current_close - recent_low) / current_close) * 10000.0
                    sl_bps = max(180.0, min(dist_to_low_bps + 40.0, 320.0))
                    tp_bps = min(dist_to_mid_bps, 450.0)

                    confidence = min(0.9, 0.55 + (drawdown - self.drop_threshold) * 2.0)

                    return ctx.signal(
                        "long",
                        confidence=round(confidence, 2),
                        stop_loss_bps=round(sl_bps, 1),
                        take_profit_bps=round(tp_bps, 1),
                        horizon_seconds=21600,
                        metadata={
                            "reason": "flush_stabilization_break_prev_high",
                            "drawdown_pct": round(drawdown * 100.0, 2),
                            "flush_high": round(recent_high, 2),
                            "flush_low": round(recent_low, 2),
                            "target_midpoint": round(flush_midpoint, 2),
                            "dist_to_mid_bps": round(dist_to_mid_bps, 1),
                            "crisis_score": round(crisis_score, 3)
                        }
                    )

        return None