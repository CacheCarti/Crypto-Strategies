from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class MomentumOrderFlowBreakout(Strategy):
    METADATA = {
        "name": "BTC Momentum Order Flow Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 24
        self.ema_fast_period = 9
        self.ema_slow_period = 26
        self.imbalance_thresh = 0.12
        self.cooldown_bars = 14
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_len = max(self.channel_period + 5, self.ema_slow_period + 5)
        closes = ctx.closes(req_len)
        highs = ctx.highs(req_len)
        lows = ctx.lows(req_len)

        if len(closes) < req_len:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]
        book_imb = ctx.features.get("book_imbalance_btcusdt", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()

            # Robust momentum stall: exit only on trend EMA crossover or strong counter-close through slow EMA
            if direction == "long":
                if ema_fast < ema_slow or current_close < ema_slow:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_momentum_exhausted",
                            "close": current_close,
                            "ema_fast": round(ema_fast, 2),
                            "ema_slow": round(ema_slow, 2),
                            "book_imb": round(book_imb, 4),
                        }
                    )
            elif direction == "short":
                if ema_fast > ema_slow or current_close > ema_slow:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_momentum_exhausted",
                            "close": current_close,
                            "ema_fast": round(ema_fast, 2),
                            "ema_slow": round(ema_slow, 2),
                            "book_imb": round(book_imb, 4),
                        }
                    )
            return None

        # Strict multi-bar cooldown to prevent overtrading & friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out high crisis regimes
        if crisis_score > 0.60:
            return None

        # Reference window: 24 bars excluding the current bar
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]

        highest_high = max(prior_highs)
        lowest_low = min(prior_lows)

        # Long Entry: Fresh breakout above 24-bar high + order book tilt + trend confirmation
        is_fresh_breakout_high = (prev_close <= highest_high) and (current_close > highest_high)
        if is_fresh_breakout_high and book_imb > self.imbalance_thresh and ema_fast > ema_slow:
            confidence = min(0.85, 0.55 + max(0.0, book_imb) * 0.35)
            return ctx.signal(
                "long",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fresh_24h_high_breakout_with_order_flow",
                    "close": current_close,
                    "breakout_level": round(highest_high, 2),
                    "book_imb": round(book_imb, 4),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                }
            )

        # Short Entry: Fresh breakdown below 24-bar low + order book tilt + trend confirmation
        is_fresh_breakdown_low = (prev_close >= lowest_low) and (current_close < lowest_low)
        if is_fresh_breakdown_low and book_imb < -self.imbalance_thresh and ema_fast < ema_slow:
            confidence = min(0.85, 0.55 + max(0.0, -book_imb) * 0.35)
            return ctx.signal(
                "short",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fresh_24h_low_breakdown_with_order_flow",
                    "close": current_close,
                    "breakdown_level": round(lowest_low, 2),
                    "book_imb": round(book_imb, 4),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                }
            )

        return None