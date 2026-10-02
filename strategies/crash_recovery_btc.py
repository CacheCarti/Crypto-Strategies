from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BTC Panic Recovery Capitulation",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 20
        self.flush_threshold_pct = 0.022  # 2.2% drop from recent high (achievable on BTC)
        self.max_hold_bars = 12
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.bars_in_pos = 0
        self.midpoint_target = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_close = ctx.bar.close
        current_idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1

            # Midpoint target exit
            if self.midpoint_target > 0.0 and current_close >= self.midpoint_target:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "close": round(current_close, 2),
                        "midpoint": round(self.midpoint_target, 2),
                        "bars_held": self.bars_in_pos,
                    }
                )

            # Max hold time exit
            if self.bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "max_recovery_hold_reached",
                        "close": round(current_close, 2),
                        "bars_held": self.bars_in_pos,
                    }
                )

            return None

        # Reset position counter when flat
        self.bars_in_pos = 0

        # Mandatory cooldown
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety: avoid entering if fear & greed is at extreme euphoria peak
        fgi = ctx.features.get("fear_greed_index", 50.0)
        if fgi > 85.0:
            return None

        # Calculate high across the lookback window
        win_highs = highs[-self.lookback_bars:]
        win_lows = lows[-self.lookback_bars:]
        highest_win = max(win_highs)
        lowest_win = min(win_lows)

        if highest_win <= 0:
            return None

        # Measure drawdown from window high to recent dip low
        recent_dip = min(lows[-3:])
        drawdown_pct = (highest_win - recent_dip) / highest_win

        # Entry Condition 1: Meaningful flush happened in the recent window
        is_flush = drawdown_pct >= self.flush_threshold_pct

        # Entry Condition 2: Capitulation reversal bar (close exceeds previous bar's high or strong bullish recovery)
        prev_high = highs[-2]
        prev_close = closes[-2]
        is_reversal_bar = (current_close > prev_high) or (current_close > prev_close * 1.004 and ctx.bar.close > ctx.bar.open)

        if is_flush and is_reversal_bar:
            self.midpoint_target = (highest_win + lowest_win) / 2.0

            # Scale confidence cleanly with flush depth
            confidence = min(0.90, max(0.60, 0.60 + (drawdown_pct - self.flush_threshold_pct) * 5.0))

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_capitulation_bounce",
                    "drawdown_pct": round(drawdown_pct * 100.0, 2),
                    "close": round(current_close, 2),
                    "window_high": round(highest_win, 2),
                    "window_low": round(lowest_win, 2),
                    "midpoint_target": round(self.midpoint_target, 2),
                    "fgi": fgi,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_pos = 0
        self.midpoint_target = 0.0