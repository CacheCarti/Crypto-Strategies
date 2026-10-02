from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleTouchSupportBounce(Strategy):
    METADATA = {
        "name": "Triple Touch Support Bounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 48
        self.touch_tolerance_pct = 0.012
        self.min_touch_spacing = 3
        self.cooldown_bars = 6
        self.rsi_period = 14
        self.last_exit_bar = -999

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

    def _count_support_touches(self, lows, closes, support_level, tolerance_pct):
        touches = []
        upper_threshold = support_level * (1.0 + tolerance_pct)
        lower_threshold = support_level * 0.988
        for i in range(len(lows)):
            if lows[i] <= upper_threshold and closes[i] >= lower_threshold:
                if not touches or (i - touches[-1]) >= self.min_touch_spacing:
                    touches.append(i)
        return len(touches)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 1)
        lows = ctx.lows(self.lookback_bars + 1)
        opens = ctx.opens(self.lookback_bars + 1)
        highs = ctx.highs(self.lookback_bars + 1)

        if len(closes) < self.lookback_bars + 1:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        # Position Management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if rsi_val >= 70.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "rsi_overbought_exit",
                            "rsi": round(rsi_val, 2),
                            "close": round(ctx.bar.close, 2),
                        },
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Extreme regime gate
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.85 or market_regime == "MELTDOWN":
            return None

        # Calculate support level across lookback window (excluding current bar)
        hist_lows = lows[:-1]
        hist_closes = closes[:-1]
        support_level = min(hist_lows)

        # Count prior touches in the lookback window
        prior_touches = self._count_support_touches(
            hist_lows, hist_closes, support_level, self.touch_tolerance_pct
        )

        current_low = lows[-1]
        current_close = closes[-1]
        current_open = opens[-1]
        current_high = highs[-1]

        # 3rd touch setup: at least 2 distinct prior touches, current bar touches support zone and holds
        is_testing_support = current_low <= support_level * (1.0 + self.touch_tolerance_pct)
        did_not_break = current_close >= support_level * 0.990
        bar_range = max(current_high - current_low, 1e-6)
        bounce_showing = (current_close >= current_open) or ((current_close - current_low) / bar_range >= 0.25)

        if (2 <= prior_touches <= 4) and is_testing_support and did_not_break and bounce_showing:
            if rsi_val <= 62.0:
                dist_to_support_pct = max(0.0, (current_close - support_level) / support_level)
                sl_bps = max(180.0, min(260.0, (dist_to_support_pct * 10000.0) + 80.0))
                tp_bps = max(360.0, sl_bps * 2.1)

                fear_greed = ctx.features.get("fear_greed_index", 50)
                confidence = 0.70
                if fear_greed < 45:
                    confidence += 0.10
                if current_close > current_open:
                    confidence += 0.05
                confidence = min(0.90, confidence)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "triple_touch_support_bounce",
                        "support_level": round(support_level, 2),
                        "prior_touches": prior_touches,
                        "rsi": round(rsi_val, 2),
                        "fear_greed": fear_greed,
                        "close": round(current_close, 2),
                    },
                )

        return None