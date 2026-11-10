from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "SolPanicFlushRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = 0.065          # 6.5% drop over 24h window
        self.cooldown_period = 8          # bars to wait after exit
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0
        self.target_midpoint = 0.0
        self.panic_low = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999
        self.entry_price = 0.0
        self.target_midpoint = 0.0
        self.panic_low = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Midpoint take-profit or stop under flush low
            if self.target_midpoint > 0 and current_price >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.75, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "price": current_price,
                    "target_midpoint": self.target_midpoint,
                    "bars_held": bars_held
                })

            if self.panic_low > 0 and current_price < self.panic_low:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "stop_loss_below_panic_low",
                    "price": current_price,
                    "panic_low": self.panic_low,
                    "bars_held": bars_held
                })

            # Time-based expiration fallback (24 hours)
            if bars_held >= 24:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "hold_horizon_reached",
                    "price": current_price,
                    "bars_held": bars_held
                })
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Calculate flush across the rolling 24-bar window
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        prev_close_24 = closes[-self.lookback]

        drop_from_high = (window_high - current_price) / window_high
        cum_ret_24 = (current_price - prev_close_24) / prev_close_24

        # Stabilization confirmation: close breaks above prior bar's high
        prev_high = highs[-2]
        confirmed_bounce = current_price > prev_high and closes[-2] <= highs[-3]

        # Indicator & regime filter to avoid falling knives in catastrophic meltdowns
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        rsi = self._rsi(closes, period=14)

        # Trigger logic: Deep flush (>6.5%) with oversold conditions followed by structural stabilization
        flush_detected = (drop_from_high >= self.drop_thresh) or (cum_ret_24 <= -self.drop_thresh)
        if flush_detected and confirmed_bounce and rsi < 42.0:
            midpoint = (window_high + window_low) / 2.0
            
            # Ensure favorable risk/reward vs midpoint target
            if midpoint > current_price * 1.015:
                stop_bps = min(500.0, max(250.0, ((current_price - window_low) / current_price) * 10000.0 + 30.0))
                tp_bps = min(750.0, max(300.0, ((midpoint - current_price) / current_price) * 10000.0))

                self.entry_bar = ctx.bar_index
                self.entry_price = current_price
                self.target_midpoint = midpoint
                self.panic_low = window_low

                confidence = min(0.90, max(0.60, 0.65 + (drop_from_high * 2.0)))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=86400,
                    metadata={
                        "reason": "panic_flush_stabilization_rebound",
                        "price": current_price,
                        "drop_from_high_pct": round(drop_from_high * 100, 2),
                        "window_high": round(window_high, 2),
                        "window_low": round(window_low, 2),
                        "target_midpoint": round(midpoint, 2),
                        "rsi": round(rsi, 2)
                    }
                )

        return None