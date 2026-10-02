from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Flush Recovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.min_flush_pct = 2.8
        self.cooldown_bars = 5
        self.last_exit_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.lookback_bars + 15
        closes = ctx.closes(needed_bars)
        highs = ctx.highs(needed_bars)
        lows = ctx.lows(needed_bars)

        if len(closes) < needed_bars:
            return None

        current_price = ctx.bar.close
        fear_greed = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")

        # In-position management: take profit at flush midpoint or overbought RSI
        if ctx.has_position():
            if ctx.position_direction() == "long":
                rsi = self._rsi(closes, 14) or 50.0
                hit_midpoint = (self.target_midpoint > 0.0 and current_price >= self.target_midpoint)
                rsi_overbought = rsi >= 66.0

                if hit_midpoint or rsi_overbought:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "flush_midpoint_or_rsi_target_reached",
                            "price": round(current_price, 2),
                            "target_midpoint": round(self.target_midpoint, 2),
                            "rsi": round(rsi, 2),
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries during meltdown
        if regime == "MELTDOWN":
            return None

        # Measure recent drop from highest peak to lowest trough within the lookback window
        window_highs = highs[-self.lookback_bars:]
        window_lows = lows[-self.lookback_bars:]
        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0:
            return None

        flush_pct = ((peak_high - trough_low) / peak_high) * 100.0

        # Stabilization trigger:
        # 1. Substantial flush occurred across the lookback window
        # 2. Current bar closes above the prior bar's high (reversal signal)
        # 3. Bar is bullish (close > open)
        prev_high = highs[-2]
        is_stabilizing = (current_price > prev_high) and (ctx.bar.close > ctx.bar.open)

        # Ensure we are entering near the bottom half of the flush range, not at the top
        midpoint = trough_low + (peak_high - trough_low) * 0.50
        in_lower_half = current_price < midpoint

        if flush_pct >= self.min_flush_pct and is_stabilizing and in_lower_half:
            self.flush_high = peak_high
            self.flush_low = trough_low
            self.target_midpoint = midpoint

            rsi_val = self._rsi(closes, 14) or 50.0
            confidence = 0.65
            if flush_pct >= 4.5:
                confidence = min(0.90, confidence + 0.15)
            if fear_greed < 35:
                confidence = min(0.95, confidence + 0.10)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "panic_flush_stabilization_entry",
                    "flush_pct": round(flush_pct, 2),
                    "peak_high": round(peak_high, 2),
                    "trough_low": round(trough_low, 2),
                    "target_midpoint": round(self.target_midpoint, 2),
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fear_greed,
                    "price": round(current_price, 2),
                }
            )

        return None