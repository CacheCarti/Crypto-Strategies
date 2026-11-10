from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.045
        self.cooldown_period = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0
        self.base_sl_price = 0.0

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
        self.target_price = 0.0
        self.base_sl_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Check existing position management
        if ctx.has_position():
            curr_close = ctx.bar.close
            bars_held = ctx.bar_index - self.entry_bar
            
            # Take profit at flush range midpoint or dynamic timeout
            if self.target_price > 0 and curr_close >= self.target_price:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_tp_reached",
                    "target_price": self.target_price,
                    "close": curr_close,
                    "bars_held": bars_held
                })

            if self.base_sl_price > 0 and curr_close <= self.base_sl_price:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "flush_structural_sl_breached",
                    "sl_price": self.base_sl_price,
                    "close": curr_close,
                    "bars_held": bars_held
                })

            if bars_held >= 18:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "recovery_horizon_timeout",
                    "close": curr_close,
                    "bars_held": bars_held
                })

            return None

        # Mandatory cooldown between exit and next entry
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Regime safety: avoid absolute meltdown/crisis freezes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN",):
            return None

        # 24-bar drop calculation (measuring from high of the window to recent low)
        highest_24 = max(highs[-self.lookback - 1:-1])
        recent_low = min(lows[-self.lookback - 1:])
        cur_close = ctx.bar.close
        prev_high = highs[-2]
        prev_low = lows[-2]

        drop_pct = (cur_close - highest_24) / highest_24
        rsi = self._rsi(closes, 14)

        # Conditions:
        # 1. Significant flush of >= 4.5% over the 24-bar lookback
        # 2. Oversold momentum condition (RSI < 38)
        # 3. Stabilization trigger: current bar closes above previous bar's high
        if drop_pct <= self.drop_thresh and rsi is not None and rsi < 38.0:
            if cur_close > prev_high:
                # Flush range midpoint target
                flush_range = highest_24 - recent_low
                midpoint_tp = recent_low + (flush_range * 0.50)
                
                # Minimum viable TP check (at least 150 bps to clear friction)
                expected_gain_bps = ((midpoint_tp - cur_close) / cur_close) * 10000.0
                if expected_gain_bps < 120.0:
                    midpoint_tp = cur_close * 1.025

                # Risk defined just below the flush stabilization pivot low
                stop_loss_price = min(prev_low, lows[-1]) * 0.992
                sl_bps = max(180.0, min(400.0, ((cur_close - stop_loss_price) / cur_close) * 10000.0))
                tp_bps = max(150.0, min(450.0, ((midpoint_tp - cur_close) / cur_close) * 10000.0))

                self.target_price = midpoint_tp
                self.base_sl_price = stop_loss_price
                self.entry_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "flush_recovery_stabilization_cross",
                        "drop_pct": round(drop_pct * 100, 2),
                        "rsi": round(rsi, 2),
                        "prev_high": prev_high,
                        "close": cur_close,
                        "target_midpoint": round(midpoint_tp, 2)
                    }
                )

        return None