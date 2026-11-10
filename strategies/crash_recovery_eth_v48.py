from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = -0.05
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.midpoint_target = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.midpoint_target = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close
        prior_high = highs[-2]
        prior_low = lows[-2]

        if ctx.has_position():
            if self.midpoint_target > 0 and current_close >= self.midpoint_target:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "close": current_close,
                        "target": self.midpoint_target,
                        "flush_high": self.flush_high,
                        "flush_low": self.flush_low
                    }
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.85 or regime == "MELTDOWN":
            return None

        past_close = closes[-(self.lookback + 1)]
        ret_24 = (current_close - past_close) / past_close

        if ret_24 <= self.drop_threshold:
            stabilized = current_close > prior_high and ctx.bar.close > ctx.bar.open
            
            if stabilized:
                recent_highs = highs[-self.lookback:]
                recent_lows = lows[-self.lookback:]
                self.flush_high = max(recent_highs)
                self.flush_low = min(recent_lows)
                self.midpoint_target = (self.flush_high + self.flush_low) / 2.0

                if self.midpoint_target > current_close:
                    distance_to_target_bps = ((self.midpoint_target - current_close) / current_close) * 10000.0
                    tp_bps = min(max(distance_to_target_bps, 150.0), 450.0)
                    sl_bps = 280.0

                    confidence = min(0.90, max(0.55, 0.55 + abs(ret_24) * 2.0))

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "panic_flush_stabilization_bounce",
                            "ret_24": round(ret_24, 4),
                            "flush_high": round(self.flush_high, 2),
                            "flush_low": round(self.flush_low, 2),
                            "target_price": round(self.midpoint_target, 2),
                            "crisis_score": round(crisis_score, 3)
                        }
                    )

        return None