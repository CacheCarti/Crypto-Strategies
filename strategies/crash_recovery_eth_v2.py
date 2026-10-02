from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Flush Recovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_flush = 20
        self.min_drop_pct = 0.025
        self.rsi_period = 14
        self.rsi_oversold_max = 48.0
        self.cooldown_bars = 3
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_flush + 15)
        highs = ctx.highs(self.lookback_flush + 15)
        lows = ctx.lows(self.lookback_flush + 15)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            midpoint = (self.flush_high + self.flush_low) / 2.0 if (self.flush_high > self.flush_low > 0) else 0.0
            
            should_exit = False
            exit_reason = ""
            
            if midpoint > 0 and current_price >= midpoint:
                should_exit = True
                exit_reason = "target_midpoint_rebound_hit"
            elif rsi >= 55.0:
                should_exit = True
                exit_reason = "rsi_mean_reversion_target"
            elif bars_held >= self.max_hold_bars:
                should_exit = True
                exit_reason = "max_time_horizon_reached"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "midpoint": round(midpoint, 2),
                        "bars_held": bars_held
                    }
                )
            return None

        # Check cooldown after previous exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate recent flush over lookback window
        recent_highs = highs[-self.lookback_flush:]
        recent_lows = lows[-self.lookback_flush:]
        period_high = max(recent_highs)
        period_low = min(recent_lows)

        if period_high <= 0:
            return None

        drop_pct = (period_high - current_price) / period_high

        # Loosened stabilization check:
        # Bullish stabilization candle (close > open and close > previous close) or close > previous high
        is_bullish_bounce = (ctx.bar.close > ctx.bar.open) and (ctx.bar.close > closes[-2])
        is_breakout_high = current_price >= highs[-2]
        is_stabilized = is_bullish_bounce or is_breakout_high

        # Entry criteria
        is_flushed = drop_pct >= self.min_drop_pct
        is_oversold = rsi <= self.rsi_oversold_max

        if is_flushed and is_oversold and is_stabilized:
            self.flush_high = period_high
            self.flush_low = period_low
            self.entry_bar = ctx.bar_index

            confidence = 0.65
            if drop_pct >= 0.045:
                confidence += 0.15
            if fear_greed < 35:
                confidence += 0.10
            confidence = min(confidence, 0.90)

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_stabilization_rebound",
                    "rsi": round(rsi, 2),
                    "drop_pct": round(drop_pct * 100, 2),
                    "flush_high": round(period_high, 2),
                    "flush_low": round(period_low, 2),
                    "fear_greed": fear_greed,
                    "price": current_price
                }
            )

        return None