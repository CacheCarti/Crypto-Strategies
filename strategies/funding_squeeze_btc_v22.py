from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold for swing squeeze
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Mandatory cooldown between trades to preserve alpha and control trade count
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in crisis/meltdown regimes where funding signals distort
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Prior evidence gate: only trade squeezes aligned with broader trend or neutral transitions
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # 24-bar price momentum
        past_close = closes[-self.lookback]
        curr_close = closes[-1]
        price_ret_pct = (curr_close - past_close) / past_close

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Volatility check to ensure sufficient expansion for squeeze continuation
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or curr_close == 0:
            return None
        atr_pct = atr / curr_close

        # Volatility must be meaningful but not extreme
        if atr_pct < 0.003 or atr_pct > 0.04:
            return None

        # Bullish Squeeze Setup:
        # Price steadily rising over 24h (+1.25% min) while funding remains negative (< -0.00005)
        # Shorts trapped paying positive cost of borrow against rising tape
        if price_ret_pct > 0.0125 and funding < -0.00005:
            if not ctx.has_position() and trend_regime != "bear":
                conf = min(0.90, 0.65 + abs(funding) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_divergence",
                        "price_ret_24h": round(price_ret_pct, 4),
                        "funding": funding,
                        "atr_pct": round(atr_pct, 4),
                        "trend_regime": trend_regime
                    }
                )

        # Bearish Long Squeeze Setup:
        # Price drifting down over 24h (-1.25% min) while funding remains positive (> 0.0001)
        # Overleveraged longs trapped bleeding fees into weakness
        if price_ret_pct < -0.0125 and funding > 0.0001:
            if not ctx.has_position() and trend_regime != "bull":
                conf = min(0.90, 0.65 + abs(funding) * 2000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_liquidation_squeeze_divergence",
                        "price_ret_24h": round(price_ret_pct, 4),
                        "funding": funding,
                        "atr_pct": round(atr_pct, 4),
                        "trend_regime": trend_regime
                    }
                )

        # Early exit if divergence collapses
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If long and funding flips heavily positive (> 0.0002), squeeze is over
            if pos_dir == "long" and funding > 0.0002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_squeeze_crowded_funding_flip", "funding": funding}
                )
            # If short and funding flips heavily negative (< -0.00015), squeeze is over
            if pos_dir == "short" and funding < -0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_squeeze_crowded_funding_flip", "funding": funding}
                )

        return None