from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.in_event = False
        self.event_start_bar = -999

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
        self.in_event = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        if len(closes) < 26:
            return None

        current_price = ctx.bar.close
        idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            # Check midpoint exit target if event metrics exist
            if self.flush_high > self.flush_low > 0:
                midpoint = (self.flush_high + self.flush_low) / 2.0
                if current_price >= midpoint:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "reached_flush_midpoint",
                            "midpoint": round(midpoint, 2),
                            "price": round(current_price, 2),
                        },
                    )
            return None

        # Respect hard post-exit cooldown
        if idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Macro safety check: avoid meltdown regimes
        if ctx.regime == "crisis" or ctx.market.get("regime", "NORMAL") == "MELTDOWN":
            return None

        # Lookback 24 bars for drop evaluation
        ref_price = closes[-25]
        rolling_high = max(highs[-25:])
        drop_pct = (current_price - rolling_high) / rolling_high

        # 1. Flush Identification: Drop >= 5% in last 24h
        if drop_pct <= -0.05:
            self.in_event = True
            self.flush_high = rolling_high
            self.flush_low = min(lows[-25:])
            self.event_start_bar = idx

        # If not tracking an active flush, pass
        if not self.in_event:
            return None

        # Event expiration guard (flush must stabilize within 10 bars)
        if idx - self.event_start_bar > 10:
            self.in_event = False
            return None

        # Update local bottom of the flush
        self.flush_low = min(self.flush_low, ctx.bar.low)

        # 2. Stabilization Trigger: Current close > Previous high
        prev_high = highs[-2]
        is_stabilized = ctx.bar.close > prev_high

        # Complementary oversold/momentum check
        rsi = self._rsi(closes, period=14)
        rsi_val = rsi if rsi is not None else 50.0
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Entry condition
        if is_stabilized and rsi_val < 45.0:
            self.in_event = False  # Reset event after triggering entry
            range_bps = ((self.flush_high - self.flush_low) / self.flush_low) * 10000.0
            
            # Dynamic TP targeting the midpoint (~50% retracement of the flush)
            midpoint = (self.flush_high + self.flush_low) / 2.0
            target_bps = max(150.0, min(500.0, ((midpoint - current_price) / current_price) * 10000.0))
            
            confidence = 0.65
            if fear_greed < 30:
                confidence += 0.15

            return ctx.signal(
                "long",
                confidence=min(0.9, confidence),
                stop_loss_bps=300.0,
                take_profit_bps=target_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "panic_flush_stabilization_cross",
                    "drop_pct": round(drop_pct * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "target_bps": round(target_bps, 1),
                    "fear_greed": fear_greed,
                    "price": current_price,
                    "prev_high": prev_high,
                },
            )

        return None