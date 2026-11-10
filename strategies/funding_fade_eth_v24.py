from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 12
        self.extreme_neg_funding = -0.00018  # -0.018%
        self.extreme_pos_funding = 0.00030   # +0.030%
        self.norm_neg_thresh = -0.00003
        self.norm_pos_thresh = 0.00008

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_fr = ctx.features.get("funding_rate_ethusdt", 0.0001)
        self.funding_history.append(current_fr)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # In-position management: normalisation exit
        if ctx.has_position():
            direction = ctx.position_direction()
            should_exit = False
            exit_reason = ""
            if direction == "long" and current_fr >= self.norm_neg_thresh:
                should_exit = True
                exit_reason = "funding_normalized_long"
            elif direction == "short" and current_fr <= self.norm_pos_thresh:
                should_exit = True
                exit_reason = "funding_normalized_short"

            if should_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": exit_reason, "funding": current_fr, "close": ctx.bar.close}
                )
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid fading crashes/meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Need persistence: funding must stay elevated/depressed across recent 3 bars
        if len(self.funding_history) < 3:
            return None
        recent_3 = self.funding_history[-3:]

        # Price filters
        sma20 = self._sma(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if sma20 is None or rsi14 is None:
            return None

        price = ctx.bar.close
        # Long entry: crowd heavily short (funding sustained negative), avoiding severe runaway downtrend
        # Require RSI > 28 (not free falling) and price within 3.5% of SMA20
        if all(f <= self.extreme_neg_funding for f in recent_3):
            price_dist = (price - sma20) / sma20
            if price_dist > -0.035 and rsi14 > 28.0 and rsi14 < 60.0:
                conf = min(0.85, 0.55 + abs(recent_3[-1]) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "funding_squeeze_long",
                        "avg_funding_3": sum(recent_3) / 3.0,
                        "rsi": rsi14,
                        "price": price,
                        "sma20": sma20,
                    }
                )

        # Short entry: crowd heavily long (funding sustained positive), avoiding runaway parabolic pump
        # Require RSI < 72 (not in vertical squeeze) and price within 3.5% of SMA20
        if all(f >= self.extreme_pos_funding for f in recent_3):
            price_dist = (price - sma20) / sma20
            if price_dist < 0.035 and rsi14 < 72.0 and rsi14 > 40.0:
                conf = min(0.85, 0.55 + recent_3[-1] * 800.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "funding_fade_short",
                        "avg_funding_3": sum(recent_3) / 3.0,
                        "rsi": rsi14,
                        "price": price,
                        "sma20": sma20,
                    }
                )

        return None