from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SOL Panic Recovery Swing",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.min_drop_pct = 0.045  # 4.5% peak-to-trough drop in SOL
        self.cooldown_bars = 3
        self.max_hold_bars = 12
        
        self.last_exit_bar = -100
        self.entry_bar = -100
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
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 15)
        highs = ctx.highs(self.lookback_bars + 15)
        lows = ctx.lows(self.lookback_bars + 15)

        if len(closes) < self.lookback_bars + 2:
            return None

        current_price = ctx.bar.close
        rsi = self._rsi(closes, period=14) or 50.0
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # -----------------------------
        # Active Position Management
        # -----------------------------
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # Midpoint target exit
            if self.target_midpoint > 0.0 and current_price >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "price": current_price,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                    }
                )

            # Time-based expiration exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_bars_reached",
                        "price": current_price,
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                    }
                )

            return None

        # -----------------------------
        # Entry Logic
        # -----------------------------
        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate recent flush metrics
        recent_highs = highs[-self.lookback_bars:]
        recent_lows = lows[-self.lookback_bars:]
        flush_high = max(recent_highs)
        flush_low = min(recent_lows)

        if flush_high <= 0.0 or flush_low <= 0.0:
            return None

        total_flush_pct = (flush_high - flush_low) / flush_high
        discount_from_high = (flush_high - current_price) / flush_high

        # 1. Has suffered a sharp flush (>= 4.5% total drop across the lookback window)
        # 2. Still trading at least 2.5% below the recent high (not chasing after full recovery)
        # 3. Stabilization trigger: bar closes above prior bar's high and is a bullish candle
        # 4. Loosened RSI check: RSI under 48 (oversold / early recovery zone)
        is_flush = total_flush_pct >= self.min_drop_pct and discount_from_high >= 0.025
        is_stabilization = ctx.bar.close > highs[-2] and ctx.bar.close > ctx.bar.open
        is_favorable_rsi = rsi <= 48.0

        if is_flush and is_stabilization and is_favorable_rsi:
            self.entry_bar = ctx.bar_index
            self.flush_high = flush_high
            self.flush_low = flush_low
            self.target_midpoint = (flush_high + flush_low) / 2.0

            # Scale confidence based on oversold depth & sentiment
            base_conf = 0.65
            if rsi < 35.0:
                base_conf += 0.15
            if fear_greed < 35.0:
                base_conf += 0.10
            confidence = min(0.95, base_conf)

            # Dynamic stop loss just under panic low
            stop_distance = max(0.018, (current_price - flush_low) / current_price)
            sl_bps = min(450.0, max(220.0, stop_distance * 10000.0 + 30.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_panic_reversal_stabilization",
                    "total_flush_pct": round(total_flush_pct * 100, 2),
                    "discount_from_high": round(discount_from_high * 100, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                    "flush_high": flush_high,
                    "flush_low": flush_low,
                    "target_midpoint": round(self.target_midpoint, 2),
                    "price": current_price,
                }
            )

        return None