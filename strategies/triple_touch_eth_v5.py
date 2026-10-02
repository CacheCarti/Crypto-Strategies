from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SupportTripleTestBounce(Strategy):
    METADATA = {
        "name": "SupportTripleTestBounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.zone_tolerance = 0.0045   # Tightened to 45 bps zone from lowest low
        self.touch_separation = 6       # Spaced out: at least 6 bars between prior touches
        self.cooldown_bars = 14         # 14-bar hard post-exit cooldown
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def _count_distinct_touches(self, lows, zone_ceiling: float) -> int:
        touch_indices = [i for i, l in enumerate(lows) if l <= zone_ceiling]
        if not touch_indices:
            return 0
        distinct_touches = 1
        last_idx = touch_indices[0]
        for idx in touch_indices[1:]:
            if idx - last_idx >= self.touch_separation:
                distinct_touches += 1
                last_idx = idx
        return distinct_touches

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(self.lookback + 20)
        lows = ctx.lows(self.lookback + 20)

        if len(closes) < self.lookback + 15:
            return None

        # Position management: let TP/SL work; exit early only on extreme exhaustion
        if ctx.has_position():
            rsi = self._rsi(closes, 14)
            if rsi is not None and rsi >= 82.0:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={"reason": "rsi_extreme_overbought_exit", "rsi": round(rsi, 2), "price": ctx.bar.close},
                )
            return None

        # Hard cooldowns to eliminate overtrading and friction churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Market regime filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Lookback analysis for support base
        history_lows = lows[-self.lookback - 1 : -1]
        if not history_lows:
            return None

        min_low = min(history_lows)
        if min_low <= 0:
            return None

        zone_ceiling = min_low * (1.0 + self.zone_tolerance)
        prior_touches = self._count_distinct_touches(history_lows[:-1], zone_ceiling)

        # Require exactly 2 prior distinct touches that held the level
        if prior_touches != 2:
            return None

        current_low = ctx.bar.low
        current_high = ctx.bar.high
        current_close = ctx.bar.close
        current_open = ctx.bar.open
        bar_range = current_high - current_low

        if bar_range <= 0:
            return None

        # 3rd test conditions:
        # 1. Tested the tight support zone
        # 2. Maintained support: close is cleanly above base
        # 3. Rejection tail: lower wick makes up at least 30% of the bar or strong green bar
        # 4. Bullish close: close > open
        test_in_zone = current_low <= zone_ceiling
        held_support = current_close > (min_low * 1.0005)
        bullish_close = current_close > current_open
        lower_wick = min(current_open, current_close) - current_low
        has_rejection_wick = (lower_wick / bar_range) >= 0.30

        if not (test_in_zone and held_support and bullish_close and has_rejection_wick):
            return None

        # Momentum filter: only enter when recovering from depressed territory
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi < 28.0 or rsi > 48.0:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Dynamic Stop-Loss placed below support level with breathing room
        dist_to_support_bps = ((current_close - min_low) / current_close) * 10000.0
        stop_loss_bps = max(160.0, min(260.0, dist_to_support_bps + 50.0))
        take_profit_bps = max(380.0, stop_loss_bps * 2.3)

        self.last_entry_bar = ctx.bar_index

        confidence = 0.70
        if 32.0 <= rsi <= 44.0:
            confidence += 0.10
        if fear_greed < 45.0:
            confidence += 0.05

        return ctx.signal(
            "long",
            confidence=min(0.90, confidence),
            stop_loss_bps=stop_loss_bps,
            take_profit_bps=take_profit_bps,
            horizon_seconds=28800,
            metadata={
                "reason": "support_triple_test_confirmed_bounce",
                "prior_touches": prior_touches,
                "support_level": round(min_low, 2),
                "zone_ceiling": round(zone_ceiling, 2),
                "rsi": round(rsi, 2),
                "rejection_wick_ratio": round(lower_wick / bar_range, 3),
                "fear_greed": fear_greed,
                "sl_bps": round(stop_loss_bps, 1),
                "tp_bps": round(take_profit_bps, 1),
            },
        )