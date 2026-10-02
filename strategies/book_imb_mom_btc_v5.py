from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcMomentumOrderFlow(Strategy):
    METADATA = {
        "name": "BTC Momentum Order Flow Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 24
        self.trend_ema_period = 48
        self.exit_ema_period = 12
        self.cooldown_bars = 14
        self.min_book_imbalance = 0.05
        self.min_breakout_bps = 12.0
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Filter out extreme market distress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "regime": regime},
                )
            return None

        closes = ctx.closes(self.trend_ema_period + 5)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.trend_ema_period or len(highs) < self.channel_period + 2:
            return None

        current_close = ctx.bar.close
        book_imbalance = ctx.features.get("book_imbalance_btcusdt", 0.0)
        exit_ema = self._ema(closes, self.exit_ema_period)
        trend_ema = self._ema(closes, self.trend_ema_period)

        # Position exit logic: momentum stall indicated by close breaking the exit EMA
        if ctx.has_position():
            direction = ctx.position_direction()
            if exit_ema is not None:
                if direction == "long" and current_close < exit_ema:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_momentum_stall_below_ema",
                            "close": current_close,
                            "exit_ema": round(exit_ema, 2),
                            "book_imbalance": round(book_imbalance, 4),
                        },
                    )
                elif direction == "short" and current_close > exit_ema:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_momentum_stall_above_ema",
                            "close": current_close,
                            "exit_ema": round(exit_ema, 2),
                            "book_imbalance": round(book_imbalance, 4),
                        },
                    )
            return None

        # Hard cooldown enforcement after any trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if trend_ema is None:
            return None

        # Channel barriers strictly prior to the current bar
        recent_highs = highs[-(self.channel_period + 1):-1]
        recent_lows = lows[-(self.channel_period + 1):-1]

        if not recent_highs or not recent_lows:
            return None

        upper_barrier = max(recent_highs)
        lower_barrier = min(recent_lows)

        # Bullish momentum breakout: price clears barrier by margin, above macro trend, order book support
        min_long_price = upper_barrier * (1.0 + self.min_breakout_bps / 10000.0)
        if current_close > min_long_price and current_close > trend_ema and book_imbalance >= self.min_book_imbalance:
            breakout_bps = ((current_close - upper_barrier) / upper_barrier) * 10000.0
            confidence = min(0.9, 0.60 + (book_imbalance * 0.25) + min(0.15, breakout_bps / 200.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "filtered_bullish_breakout",
                    "close": current_close,
                    "upper_barrier": round(upper_barrier, 2),
                    "trend_ema": round(trend_ema, 2),
                    "book_imbalance": round(book_imbalance, 4),
                    "breakout_bps": round(breakout_bps, 2),
                },
            )

        # Bearish momentum breakdown: price breaks barrier by margin, below macro trend, order book selling pressure
        max_short_price = lower_barrier * (1.0 - self.min_breakout_bps / 10000.0)
        if current_close < max_short_price and current_close < trend_ema and book_imbalance <= -self.min_book_imbalance:
            breakdown_bps = ((lower_barrier - current_close) / lower_barrier) * 10000.0
            confidence = min(0.9, 0.60 + (-book_imbalance * 0.25) + min(0.15, breakdown_bps / 200.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "filtered_bearish_breakdown",
                    "close": current_close,
                    "lower_barrier": round(lower_barrier, 2),
                    "trend_ema": round(trend_ema, 2),
                    "book_imbalance": round(book_imbalance, 4),
                    "breakdown_bps": round(breakdown_bps, 2),
                },
            )

        return None