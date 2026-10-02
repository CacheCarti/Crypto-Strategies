from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TripleTouchSupportBounce(Strategy):
    METADATA = {
        "name": "Triple Touch Support Bounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 80,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.window = 72
        self.tolerance_bps = 40.0
        self.cooldown_bars = 24
        self.last_trade_bar = -100
        self.last_exit_bar = -100

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

    def _count_prior_touches(self, lows, min_low, thresh, min_separation=8):
        # Identify distinct historical touches separated by at least min_separation bars
        touches = 0
        last_touch_idx = -100
        # Exclude the most recent 2 bars to evaluate prior structure
        limit = len(lows) - 2
        for i in range(limit):
            if lows[i] <= thresh and lows[i] >= min_low * 0.998:
                if (i - last_touch_idx) >= min_separation:
                    touches += 1
                    last_touch_idx = i
        return touches

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.window + 30)
        lows = ctx.lows(self.window + 30)
        highs = ctx.highs(self.window + 30)
        opens = ctx.opens(self.window + 30)

        if len(closes) < self.window + 20:
            return None

        rsi_val = self._rsi(closes, 14)
        if rsi_val is None:
            return None

        # Position management: discretionary take profit on strong momentum exhaustion
        if ctx.has_position():
            if rsi_val >= 78.0:
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "rsi_overbought_exit",
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Strict Cooldown gate (prevents overtrading and friction loss)
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid choppy panics and meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL").upper()
        if crisis_score > 0.55 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Support zone identification
        w_lows = lows[-self.window :]
        min_low = min(w_lows[:-1])  # Established historical minimum
        thresh = min_low * (1.0 + (self.tolerance_bps / 10000.0))

        # Count distinct structural prior touches
        prior_touches = self._count_prior_touches(w_lows, min_low, thresh, min_separation=8)

        curr_bar = ctx.bar
        bar_range = curr_bar.high - curr_bar.low
        if bar_range <= 0.0:
            return None

        # Strict setup conditions:
        # 1. Exactly 2 distinct prior tests of the support zone
        # 2. Current bar dips into the support zone (the 3rd test) without breaking below it significantly
        # 3. Strong rejection candle: bullish close in the upper half of the bar with a substantial lower wick
        is_current_touch = (curr_bar.low <= thresh) and (curr_bar.low >= min_low * 0.997)
        is_bullish_close = curr_bar.close > curr_bar.open
        closes_in_upper_half = curr_bar.close >= (curr_bar.low + 0.50 * bar_range)
        lower_wick = min(curr_bar.open, curr_bar.close) - curr_bar.low
        has_rejection_wick = lower_wick >= (0.30 * bar_range)
        rsi_in_rebound_zone = 25.0 <= rsi_val <= 48.0

        if (
            prior_touches == 2
            and is_current_touch
            and is_bullish_close
            and closes_in_upper_half
            and has_rejection_wick
            and rsi_in_rebound_zone
        ):
            # Dynamic stop placement safely under structural support
            dist_to_support_bps = ((curr_bar.close - min_low * 0.996) / curr_bar.close) * 10000.0
            stop_loss_bps = max(180.0, min(dist_to_support_bps + 50.0, 320.0))
            take_profit_bps = max(420.0, stop_loss_bps * 2.2)

            fg_index = ctx.features.get("fear_greed_index", 50.0)
            confidence = 0.70
            if fg_index <= 35.0:
                confidence = 0.85
            elif fg_index >= 65.0:
                confidence = 0.65

            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                metadata={
                    "reason": "triple_touch_support_rejection",
                    "support_level": round(min_low, 2),
                    "prior_touches": prior_touches,
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fg_index,
                    "stop_bps": round(stop_loss_bps, 1),
                    "tp_bps": round(take_profit_bps, 1),
                },
            )

        return None