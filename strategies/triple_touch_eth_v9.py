from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleTouchSupportBounce(Strategy):
    METADATA = {
        "name": "TripleTouchSupportBounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_window = 48
        self.tolerance_pct = 0.0040  # Tight 40 bps zone to ensure clean support
        self.cooldown_bars = 16       # 16-bar hard cooldown after trades/exits
        self.last_trade_bar = -999
        self.min_touch_spacing = 8   # Require distinct swing lows across the window

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

    def _count_prior_touches(self, lows, closes, support_level, tolerance):
        touches = 0
        last_touch_idx = -999
        upper_bound = support_level * (1.0 + tolerance)

        for i in range(len(lows)):
            if lows[i] <= upper_bound and closes[i] >= support_level * 0.9985:
                if i - last_touch_idx >= self.min_touch_spacing:
                    touches += 1
                    last_touch_idx = i
        return touches

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Position management
        if ctx.has_position():
            closes = ctx.closes(15)
            rsi = self._rsi(closes, period=14)
            if rsi is not None and rsi >= 75.0:
                return ctx.signal("flat", confidence=0.80, metadata={
                    "reason": "rsi_overbought_exit",
                    "rsi": round(rsi, 2),
                    "close": ctx.bar.close
                })
            return None

        # Hard cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Macro & regime protection
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Need sufficient lookback history
        lows = ctx.lows(self.lookback_window)
        highs = ctx.highs(self.lookback_window)
        closes = ctx.closes(self.lookback_window)
        if len(lows) < self.lookback_window:
            return None

        # History prior to the current test (exclude the last 2 bars)
        prior_lows = lows[:-2]
        prior_closes = closes[:-2]

        support_level = min(prior_lows)
        zone_top = support_level * (1.0 + self.tolerance_pct)

        # Count prior tests with strict separation
        prior_touches = self._count_prior_touches(prior_lows, prior_closes, support_level, self.tolerance_pct)
        if prior_touches < 2:
            return None

        # Current bar confirmation: tests zone without breakdown and prints a strong hammer/bullish rejection
        curr_bar = ctx.bar
        bar_range = max(curr_bar.high - curr_bar.low, 1e-6)
        bounce_ratio = (curr_bar.close - curr_bar.low) / bar_range

        tested_zone = curr_bar.low <= zone_top and curr_bar.low >= support_level * 0.998
        bullish_close = curr_bar.close > curr_bar.open and curr_bar.close > support_level
        strong_rejection = bounce_ratio >= 0.60

        if tested_zone and bullish_close and strong_rejection:
            rsi = self._rsi(closes, period=14)
            # Require RSI to be in recovery zone (not overbought or in freefall)
            if rsi is None or rsi < 30.0 or rsi > 52.0:
                return None

            confidence = 0.75
            if rsi <= 42.0:
                confidence += 0.10
            if bounce_ratio >= 0.75:
                confidence += 0.08
            confidence = min(0.92, confidence)

            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_touch_support_rejection",
                    "support_level": round(support_level, 2),
                    "prior_touches": prior_touches,
                    "bounce_ratio": round(bounce_ratio, 3),
                    "rsi": round(rsi, 2),
                    "close": curr_bar.close,
                    "low": curr_bar.low
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index