from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OrderFlowBreakoutStrategy(Strategy):
    METADATA = {
        "name": "OrderFlowBreakoutStrategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 55,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.breakout_period = 24
        self.exit_period = 12
        self.trend_ema_period = 48
        self.cooldown_bars = 10
        self.min_book_imbalance = 0.12
        self.last_exit_bar = -999

    def _ema(self, values, period):
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
        req_bars = max(self.breakout_period, self.trend_ema_period) + 5
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        curr_close = ctx.bar.close
        book_imb = ctx.features.get("book_imbalance_btcusdt", 0.0)
        ema_trend = self._ema(closes, self.trend_ema_period)

        # 1. Position Management & Momentum Stall Exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                exit_lows = ctx.lows(self.exit_period + 1)[:-1]
                exit_level = min(exit_lows)
                if curr_close < exit_level:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "long_momentum_stall_below_n_bar_low",
                            "close": curr_close,
                            "exit_level": exit_level,
                            "book_imbalance": book_imb,
                        },
                    )
            elif pos_dir == "short":
                exit_highs = ctx.highs(self.exit_period + 1)[:-1]
                exit_level = max(exit_highs)
                if curr_close > exit_level:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "short_momentum_stall_above_n_bar_high",
                            "close": curr_close,
                            "exit_level": exit_level,
                            "book_imbalance": book_imb,
                        },
                    )
            return None

        # 2. Hard Cooldown Gate to limit churn & friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 3. Channel Range Calculation (excluding the current bar)
        highs = ctx.highs(self.breakout_period + 1)[:-1]
        lows = ctx.lows(self.breakout_period + 1)[:-1]
        channel_high = max(highs)
        channel_low = min(lows)

        # 4. Long Entry: Strict Donchian Breakout + Strong Positive Order Flow + Macro Trend Filter
        if curr_close > channel_high and book_imb >= self.min_book_imbalance:
            if ema_trend is not None and curr_close < ema_trend:
                return None
            conf = min(0.90, 0.65 + (book_imb * 0.25))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                metadata={
                    "reason": "high_conviction_breakout_positive_imbalance",
                    "close": curr_close,
                    "channel_high": channel_high,
                    "book_imbalance": book_imb,
                    "ema_trend": ema_trend or curr_close,
                },
            )

        # 5. Short Entry: Strict Donchian Breakdown + Strong Negative Order Flow + Macro Trend Filter
        if curr_close < channel_low and book_imb <= -self.min_book_imbalance:
            if ema_trend is not None and curr_close > ema_trend:
                return None
            conf = min(0.90, 0.65 + (abs(book_imb) * 0.25))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                metadata={
                    "reason": "high_conviction_breakdown_negative_imbalance",
                    "close": curr_close,
                    "channel_low": channel_low,
                    "book_imbalance": book_imb,
                    "ema_trend": ema_trend or curr_close,
                },
            )

        return None