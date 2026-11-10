from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBTC(Strategy):
    METADATA = {
        "name": "CrashRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 43200,  # 12 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16
        self.midpoint_target = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1 or len(highs) < 2:
            return None

        # Manage open position
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close
            
            # Target reached or max time hold
            hit_target = self.midpoint_target > 0 and current_close >= self.midpoint_target
            timeout = bars_in_pos >= self.max_hold_bars

            if hit_target or timeout:
                reason = "midpoint_reached" if hit_target else "max_hold_timeout"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_in_pos,
                        "target": self.midpoint_target,
                        "close": current_close
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate to prevent catching falling knives in structural meltdowns
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # 24-bar drop calculation (rolling window flush of >= 5.5% to ensure adequate sample size)
        period_high = max(highs[-self.lookback:])
        period_low = min(lows[-self.lookback:])
        current_close = ctx.bar.close
        prev_high = highs[-2]
        drop_pct = (period_high - current_close) / period_high

        # RSI filter: flush must exhibit extreme local exhaustion (RSI <= 38)
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None:
            return None

        # Setup conditions:
        # 1. 24-bar flush >= 5.5%
        # 2. Reversal trigger: current close breaks above previous bar's high
        # 3. Oversold exhaustion condition
        if drop_pct >= 0.055 and current_close > prev_high and rsi_val <= 38.0:
            self.entry_bar = ctx.bar_index
            self.midpoint_target = (period_high + period_low) / 2.0
            confidence = min(0.9, 0.6 + (drop_pct - 0.055) * 5.0)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "flush_capitulation_reversal",
                    "drop_pct": round(drop_pct * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "midpoint_target": round(self.midpoint_target, 2),
                    "prev_high": round(prev_high, 2),
                    "close": round(current_close, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.midpoint_target = 0.0