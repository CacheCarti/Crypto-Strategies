from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SupportTripleTapBounce(Strategy):
    METADATA = {
        "name": "Support Triple Tap Bounce",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 52,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.tolerance_pct = 0.0045  # 45 bps zone for touch detection
        self.min_touch_spacing = 4   # Minimum bars separating distinct tests
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_support_level = 0.0

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

    def _count_prior_touches(self, lows, closes, support_level):
        """Counts distinct prior touches that held above the support level."""
        threshold = support_level * (1.0 + self.tolerance_pct)
        touches = 0
        last_touch_idx = -999

        # Search window excluding the current bar (index -1)
        for i in range(len(lows) - 1):
            if lows[i] <= threshold and closes[i] >= support_level:
                if (i - last_touch_idx) >= self.min_touch_spacing:
                    touches += 1
                    last_touch_idx = i
        return touches

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_support_level = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_rsi = self._rsi(closes, 14)
        if current_rsi is None:
            return None

        # Position management / Exit check
        if ctx.has_position():
            # Exit if RSI reaches overbought or price invalidates support zone
            current_close = ctx.bar.close
            if current_rsi >= 72.0:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_overbought_take_profit",
                        "rsi": round(current_rsi, 2),
                        "close": current_close,
                        "entry_support": round(self.entry_support_level, 2),
                    },
                )
            if self.entry_support_level > 0.0 and current_close < (self.entry_support_level * 0.993):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "support_breakdown_stop",
                        "rsi": round(current_rsi, 2),
                        "close": current_close,
                        "entry_support": round(self.entry_support_level, 2),
                    },
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Macro/Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 15:
            return None

        # Find key support in lookback window (excluding current bar)
        window_lows = lows[-(self.lookback + 1):-1]
        window_closes = closes[-(self.lookback + 1):-1]
        support_level = min(window_lows)

        # Count prior confirmed touches in the window
        prior_touches = self._count_prior_touches(window_lows, window_closes, support_level)

        # We look for exactly 2 prior distinct touches that held
        if prior_touches == 2:
            current_low = ctx.bar.low
            current_close = ctx.bar.close
            current_open = ctx.bar.open
            tolerance_ceiling = support_level * (1.0 + self.tolerance_pct)

            # 3rd test: Low penetrates the tolerance zone, close holds above support, and bar is bullish
            tested_zone = current_low <= tolerance_ceiling
            held_support = current_close >= support_level
            bullish_bounce = current_close > current_open

            if tested_zone and held_support and bullish_bounce and current_rsi < 60.0:
                # Dynamic stop-loss placed slightly below the support zone
                sl_distance_bps = max(150.0, min(300.0, ((current_close - (support_level * 0.996)) / current_close) * 10000.0))
                tp_distance_bps = sl_distance_bps * 2.0
                confidence = 0.70 if current_rsi <= 45.0 else 0.60

                self.entry_support_level = support_level

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_distance_bps,
                    take_profit_bps=tp_distance_bps,
                    metadata={
                        "reason": "support_triple_tap_bounce",
                        "support_level": round(support_level, 2),
                        "prior_touches": prior_touches,
                        "rsi": round(current_rsi, 2),
                        "close": current_close,
                        "fear_greed": fear_greed,
                        "regime": regime,
                    },
                )

        return None