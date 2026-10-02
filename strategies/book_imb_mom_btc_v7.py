from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcMomentumOrderFlow(Strategy):
    METADATA = {
        "name": "BtcMomentumOrderFlow",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.breakout_period = 24
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 14
        self.min_hold_bars = 3
        self.imbalance_thresh = 0.06
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_len = max(self.breakout_period + 5, self.ema_slow_period + 5, self.rsi_period + 5)
        closes = ctx.closes(req_len)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)

        if len(closes) < req_len or len(highs) < self.breakout_period + 2 or len(lows) < self.breakout_period + 2:
            return None

        current_close = ctx.bar.close
        book_imb = ctx.features.get("book_imbalance_btcusdt", 0.0)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi_val is None:
            return None

        # Calculate breakout levels from previous N bars excluding current bar
        prior_high = max(highs[-self.breakout_period - 1:-1])
        prior_low = min(lows[-self.breakout_period - 1:-1])

        # Active Position Management / Exit on Momentum Stalling (with min hold protection)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_entry_bar

            if bars_held >= self.min_hold_bars:
                if pos_dir == "long":
                    # Exit on clear stall: price below EMA fast AND RSI dropping back under neutral
                    if current_close < ema_fast and rsi_val < 42.0:
                        self.last_exit_bar = ctx.bar_index
                        return ctx.signal(
                            "flat",
                            confidence=0.7,
                            metadata={
                                "reason": "long_momentum_stall",
                                "close": current_close,
                                "ema_fast": ema_fast,
                                "rsi": rsi_val,
                                "bars_held": bars_held,
                            },
                        )

                elif pos_dir == "short":
                    # Exit on clear stall: price above EMA fast AND RSI rising above neutral
                    if current_close > ema_fast and rsi_val > 58.0:
                        self.last_exit_bar = ctx.bar_index
                        return ctx.signal(
                            "flat",
                            confidence=0.7,
                            metadata={
                                "reason": "short_momentum_stall",
                                "close": current_close,
                                "ema_fast": ema_fast,
                                "rsi": rsi_val,
                                "bars_held": bars_held,
                            },
                        )
            return None

        # Hard Cooldown Filter to eliminate trade churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long Setup: Decisive 24-bar High Breakout + Strong Trend (EMA Fast > Slow) + Meaningful Order Flow Imbalance + RSI confirmation
        if (
            current_close > prior_high
            and ema_fast > ema_slow
            and book_imb > self.imbalance_thresh
            and 55.0 <= rsi_val <= 75.0
        ):
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + min(0.25, abs(book_imb)))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_breakout_orderflow_confirmed",
                    "close": current_close,
                    "prior_high": prior_high,
                    "book_imbalance": book_imb,
                    "rsi": rsi_val,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                },
            )

        # Short Setup: Decisive 24-bar Low Breakdown + Downtrend (EMA Fast < Slow) + Meaningful Negative Order Flow Imbalance + RSI confirmation
        if (
            current_close < prior_low
            and ema_fast < ema_slow
            and book_imb < -self.imbalance_thresh
            and 25.0 <= rsi_val <= 45.0
        ):
            self.last_entry_bar = ctx.bar_index
            conf = min(0.9, 0.65 + min(0.25, abs(book_imb)))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_breakdown_orderflow_confirmed",
                    "close": current_close,
                    "prior_low": prior_low,
                    "book_imbalance": book_imb,
                    "rsi": rsi_val,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                },
            )

        return None