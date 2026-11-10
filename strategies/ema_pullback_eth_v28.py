from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaCrossAssetPullback(Strategy):
    METADATA = {
        "name": "EthEmaCrossAssetPullback",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,  # ~4 hours target swing
        "warmup_bars": 60,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 5
        self.last_exit_bar = -100

    def _ema(self, values, period: int):
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
        closes = ctx.closes(self.slow_period + 5)
        lows = ctx.lows(5)
        highs = ctx.highs(5)

        if len(closes) < self.slow_period + 2:
            return None

        # Calculate Indicators
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_exit_cross_under_ema20",
                        "close": curr_close,
                        "ema20": ema20,
                    }
                )
            elif direction == "short" and curr_close > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_exit_cross_over_ema20",
                        "close": curr_close,
                        "ema20": ema20,
                    }
                )
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Cross-asset confirmation to overcome historical failure of isolated pullbacks
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Long Setup:
        # 1. Macro Trend: EMA20 > EMA50 and curr_close > EMA50
        # 2. Pullback trigger: Low pierced or touched EMA20 (or previous bar closed below), but now curr_close > EMA20
        # 3. Cross-asset confirmation: BTC 24h return is positive (> 0.2%)
        long_pullback = (prev_low <= ema20 or prev_close <= ema20) and curr_close > ema20
        if curr_close > ema50 and ema20 > ema50 and long_pullback and btc_ret > 0.2:
            confidence = min(0.9, 0.6 + max(0.0, btc_ret) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_long_btc_confirmed",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "btc_return_pct": btc_ret,
                }
            )

        # Short Setup:
        # 1. Macro Trend: EMA20 < EMA50 and curr_close < EMA50
        # 2. Pullback trigger: High pierced or touched EMA20 (or previous bar closed above), but now curr_close < EMA20
        # 3. Cross-asset confirmation: BTC 24h return is negative (< -0.2%)
        short_pullback = (prev_high >= ema20 or prev_close >= ema20) and curr_close < ema20
        if curr_close < ema50 and ema20 < ema50 and short_pullback and btc_ret < -0.2:
            confidence = min(0.9, 0.6 + max(0.0, -btc_ret) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_short_btc_confirmed",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "btc_return_pct": btc_ret,
                }
            )

        return None