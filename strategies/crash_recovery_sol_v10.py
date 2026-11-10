from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,  # ~8 hours
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_pct_threshold = -0.07  # -7% flush
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.panic_high = 0.0
        self.panic_low = 0.0
        self.flush_active = False

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_active = False

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
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

        if len(closes) < self.lookback + 2:
            return None

        # Check if already in position
        if ctx.has_position():
            # In-trade midpoint target or stop checks
            current_close = ctx.bar.close
            midpoint = (self.panic_high + self.panic_low) / 2.0 if (self.panic_high > 0 and self.panic_low > 0) else None
            
            # Midpoint take-profit condition
            if midpoint is not None and current_close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_range_midpoint_reached",
                        "price": current_close,
                        "midpoint": midpoint,
                        "panic_low": self.panic_low
                    }
                )
            
            # Stop breach below panic low
            if self.panic_low > 0 and current_close < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "panic_low_violated_hard_exit",
                        "price": current_close,
                        "panic_low": self.panic_low
                    }
                )

            return None

        # Mandatory cooldown after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime and market condition filters
        # Avoid MELTDOWN or CRISIS regimes where knives keep falling indefinitely
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.70:
            return None

        # Feature gate: Sentiment must reflect panic/fear, not euphoria
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        if fg_index > 65.0:
            return None

        # Calculate flush over 24-bar window
        highest_24 = max(highs[-self.lookback:])
        lowest_24 = min(lows[-self.lookback:])
        current_close = ctx.bar.close
        prev_close_24 = closes[-self.lookback]

        # 24-bar return
        ret_24 = (current_close - highest_24) / highest_24

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Conditions for entry:
        # 1. SOL dumped >= 7% from recent high over the last 24 bars
        # 2. RSI confirms heavy oversold pressure (< 36)
        # 3. First stabilization: Current bar closes above previous bar's high
        prev_high = highs[-2]
        is_stabilization = current_close > prev_high
        is_flush = ret_24 <= self.drop_pct_threshold

        if is_flush and is_stabilization and rsi < 36.0:
            self.panic_high = highest_24
            self.panic_low = lowest_24
            self.flush_active = True

            # Dynamic SL based on panic low distance (bounded within 250 - 500 bps)
            dist_to_low_bps = max(50.0, ((current_close - self.panic_low) / current_close) * 10000.0)
            sl_bps = min(max(dist_to_low_bps + 30.0, 250.0), 500.0)

            # Midpoint profit target in bps
            midpoint = (self.panic_high + self.panic_low) / 2.0
            tp_bps = min(max(((midpoint - current_close) / current_close) * 10000.0, 300.0), 750.0)

            # Higher confidence if RSI is extremely low
            confidence = 0.75 if rsi < 25.0 else 0.65

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_panic_flush_reversal",
                    "ret_24": round(ret_24, 4),
                    "rsi": round(rsi, 2),
                    "panic_high": round(self.panic_high, 2),
                    "panic_low": round(self.panic_low, 2),
                    "prev_high": round(prev_high, 2),
                    "price": round(current_close, 2),
                    "fg_index": fg_index
                }
            )

        return None