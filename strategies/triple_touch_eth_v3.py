from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SupportClusterBounce(Strategy):
    METADATA = {
        "name": "SupportClusterBounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 70,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.tolerance_pct = 0.0050   # 0.50% zone tolerance
        self.min_touch_spacing = 8     # Minimum bars between distinct pivot touches
        self.cooldown_bars = 20        # Hard mandatory post-exit cooldown (20 bars)
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 24

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
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _find_prior_pivot_touches(self, lows: list, support_level: float, tol_val: float) -> int:
        """Find distinct swing pivot lows near the support zone in historical bars."""
        # Search prior window excluding the most recent 2 bars
        start_idx = len(lows) - self.lookback
        end_idx = len(lows) - 2
        pivot_touches = []

        for i in range(start_idx + 2, end_idx):
            # Strict pivot low definition (+/- 2 bars)
            is_pivot = (
                lows[i] <= lows[i - 1] and lows[i] <= lows[i - 2] and
                lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]
            )
            if is_pivot and abs(lows[i] - support_level) <= tol_val:
                if not pivot_touches or (i - pivot_touches[-1]) >= self.min_touch_spacing:
                    pivot_touches.append(i)

        return len(pivot_touches)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(self.lookback + 20)
        lows = ctx.lows(self.lookback + 20)
        opens = ctx.opens(self.lookback + 20)

        if len(closes) < self.lookback + 10:
            return None

        curr_close = closes[-1]
        curr_low = lows[-1]
        curr_high = highs[-1]
        curr_open = opens[-1]

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # Position Management & Exit Rules
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            
            # Momentum exhaustion / overbought exit
            if rsi >= 70.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "rsi_overbought_exit", "rsi": round(rsi, 2), "bars_held": bars_held}
                )

            # Time-based expiration exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_exhaustion", "rsi": round(rsi, 2), "bars_held": bars_held}
                )

            return None

        # Hard post-exit cooldown filter
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime & Crisis Filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.50 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Define established historical support from prior 48 bars (excluding current bar)
        hist_lows = lows[-self.lookback:-1]
        if not hist_lows:
            return None
        support = min(hist_lows)
        tol_val = support * self.tolerance_pct

        # Test condition: price dips into support zone without closing significantly below it
        tests_support = (curr_low <= support + tol_val) and (curr_close >= support * 0.9985)

        # Bullish rejection candle (hammer/pinbar: lower wick >= 45% of candle range and green/flat body)
        candle_range = max(curr_high - curr_low, 0.001)
        lower_wick = (min(curr_open, curr_close) - curr_low)
        is_pinbar_rejection = (lower_wick / candle_range >= 0.45) and (curr_close >= curr_open * 0.999)

        # Stricter momentum gate: RSI must be in oversold/reversal zone (25 to 48)
        rsi_valid = (25.0 <= rsi <= 48.0)

        if tests_support and is_pinbar_rejection and rsi_valid:
            # Count prior confirmed pivot lows around this support level
            prior_touches = self._find_prior_pivot_touches(lows, support, tol_val)

            # Trigger only on the 3rd test (2 prior confirmed pivot bounces that held)
            if prior_touches == 2:
                dist_to_support_bps = ((curr_close - support) / curr_close) * 10000.0
                dynamic_sl = max(180.0, min(280.0, dist_to_support_bps + 60.0))
                dynamic_tp = max(380.0, dynamic_sl * 2.2)

                confidence = 0.75 if rsi > 38.0 else 0.85
                self.entry_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "triple_touch_support_bounce",
                        "support_level": round(support, 2),
                        "prior_touches": prior_touches,
                        "rsi": round(rsi, 2),
                        "close": round(curr_close, 2),
                        "sl_bps": round(dynamic_sl, 1),
                        "tp_bps": round(dynamic_tp, 1)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index