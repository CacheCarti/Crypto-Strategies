from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.055
        self.max_hold_bars = 16
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0

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
        
        if len(closes) < self.lookback + 2:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close
            midpoint = (self.flush_high + self.flush_low) / 2.0 if self.flush_high > 0 else current_close * 1.05

            if current_close >= midpoint:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "reversion_midpoint_hit", "midpoint": midpoint, "close": current_close, "bars_held": bars_held}
                )

            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_expired", "bars_held": bars_held, "close": current_close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Macro regime check via market context to avoid dead-cat bounces in ongoing meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Drop detection: High of past 24 bars to recent low
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        current_close = ctx.bar.close
        prev_high = highs[-2]

        drop_pct = (recent_high - current_close) / recent_high

        # RSI confirmation: must show oversold/divergence condition during the flush
        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Flush condition + Capitulation trigger: close back above previous bar's high
        if drop_pct >= self.drop_threshold and current_close > prev_high and rsi < 42.0:
            self.entry_bar = ctx.bar_index
            self.flush_high = recent_high
            self.flush_low = recent_low
            
            # Confidence scaled by magnitude of oversold capitulation
            conf = min(0.9, max(0.5, (drop_pct / 0.10) * 0.75))
            
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flush_capitulation_break_prev_high",
                    "drop_pct": round(drop_pct * 100, 2),
                    "rsi": round(rsi, 2),
                    "prev_high": prev_high,
                    "close": current_close,
                    "target_midpoint": round((recent_high + recent_low) / 2.0, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index