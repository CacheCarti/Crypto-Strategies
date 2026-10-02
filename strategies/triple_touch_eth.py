from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class SupportLevelTripleBounce(Strategy):
    METADATA = {
        "name": "Support Level Triple Bounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 48
        self.tolerance_pct = 0.004   # Tight 40 bps zone around support
        self.min_touch_spacing = 7    # At least 7 bars apart to confirm distinct swing tests
        self.min_rebound_pct = 0.008  # Price must bounce at least 80 bps between touches
        self.cooldown_bars = 18       # Mandatory cooldown to prevent overtrading
        self.last_entry_bar = -100
        self.last_exit_bar = -100

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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

    def _find_distinct_support_touches(
        self, highs: List[float], lows: List[float], closes: List[float], support_level: float
    ) -> List[int]:
        """Identify strictly distinct historical support tests that showed genuine intermediate rebounds."""
        upper_bound = support_level * (1.0 + self.tolerance_pct)
        touch_indices: List[int] = []

        n = len(closes)
        start_idx = max(0, n - self.lookback_bars)

        for idx in range(start_idx, n - 1):
            bar_low = lows[idx]
            bar_close = closes[idx]

            # Candle dipped into support zone but body held above baseline
            if bar_low <= upper_bound and bar_close >= support_level * 0.998:
                if not touch_indices:
                    touch_indices.append(idx)
                else:
                    prev_idx = touch_indices[-1]
                    # Verify spacing and that a clear rebound occurred between touches
                    if (idx - prev_idx) >= self.min_touch_spacing:
                        intermediate_peak = max(highs[prev_idx:idx])
                        if (intermediate_peak - support_level) / support_level >= self.min_rebound_pct:
                            touch_indices.append(idx)

        return touch_indices

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 15)
        highs = ctx.highs(self.lookback_bars + 15)
        lows = ctx.lows(self.lookback_bars + 15)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_idx = ctx.bar_index
        current_bar = ctx.bar
        current_close = current_bar.close
        current_low = current_bar.low
        current_open = current_bar.open
        current_high = current_bar.high

        rsi = self._rsi(closes, period=14)
        rsi_val = rsi if rsi is not None else 50.0

        # Regime filter: avoid taking longs during market meltdown or crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Position management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Take profit early on extreme momentum exhaustion
                if rsi_val >= 75.0:
                    self.last_exit_bar = current_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.80,
                        metadata={
                            "reason": "rsi_overbought_profit_take",
                            "rsi": round(rsi_val, 2),
                            "close": current_close,
                        },
                    )
            return None

        # Hard cooldown check after entries and exits
        if (current_idx - self.last_entry_bar < self.cooldown_bars) or \
           (current_idx - self.last_exit_bar < self.cooldown_bars):
            return None

        # Reference support: minimum low over 48 bars (excluding the active bar)
        window_lows = lows[-self.lookback_bars - 1 : -1]
        support_level = min(window_lows)
        zone_top = support_level * (1.0 + self.tolerance_pct)

        # Entry criteria for the 3rd test:
        # 1. Bar dips into the tight support band without breaking down
        is_testing = (current_low <= zone_top) and (current_low >= support_level * 0.995)
        holds_level = current_close >= support_level

        # 2. Bullish price action: green close or significant lower wick rejection
        total_range = max(current_high - current_low, 1e-6)
        lower_wick = min(current_open, current_close) - current_low
        has_wick_rejection = (lower_wick / total_range) >= 0.40
        is_green = current_close >= current_open
        confirmed_bounce = holds_level and (is_green or has_wick_rejection)

        if not (is_testing and confirmed_bounce):
            return None

        # 3. Exactly 2 prior distinct touches with intermediate bounces (making this the 3rd test)
        prior_touches = self._find_distinct_support_touches(highs, lows, closes, support_level)
        if len(prior_touches) != 2:
            return None

        # 4. Check intermediate rebound from the last touch before this test
        last_touch_idx = prior_touches[-1]
        bars_since_last = (len(closes) - 1) - last_touch_idx
        if bars_since_last < self.min_touch_spacing:
            return None

        intermediate_high = max(highs[last_touch_idx:-1])
        if (intermediate_high - support_level) / support_level < self.min_rebound_pct:
            return None

        # 5. Momentum filter: RSI must indicate oversold recovery, not strong bearish runaway
        if not (30.0 <= rsi_val <= 48.0):
            return None

        # Define dynamic tight stop just under support floor
        risk_dist = current_close - (support_level * 0.995)
        sl_bps = max(130.0, min(240.0, (risk_dist / current_close) * 10000.0))
        tp_bps = max(320.0, sl_bps * 2.2)

        self.last_entry_bar = current_idx

        return ctx.signal(
            "long",
            confidence=0.85,
            stop_loss_bps=round(sl_bps, 1),
            take_profit_bps=round(tp_bps, 1),
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "triple_support_bounce_clean_test",
                "support_level": round(support_level, 2),
                "prior_touches": len(prior_touches),
                "rsi": round(rsi_val, 2),
                "close": current_close,
                "sl_bps": round(sl_bps, 1),
                "tp_bps": round(tp_bps, 1),
            },
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index