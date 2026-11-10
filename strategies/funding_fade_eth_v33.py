from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.funding_threshold = 0.00015  # 0.015% per 8h considered elevated
        self.normalize_threshold = 0.00004
        self.persist_bars = 3

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Track persistent funding
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Skip during active market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # Exit on funding normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_funding > -self.normalize_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_long", "funding": current_funding})
            elif pos_dir == "short" and current_funding < self.normalize_threshold:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_short", "funding": current_funding})
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if len(self.funding_history) < self.persist_bars:
            return None

        recent_funding = self.funding_history[-self.persist_bars:]
        sma30 = self._sma(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if sma30 is None or rsi14 is None:
            return None

        price = closes[-1]
        pct_from_sma = (price - sma30) / sma30

        # Long squeeze condition: persistent negative funding, price not deeply trending down
        persistent_negative = all(f <= -self.funding_threshold for f in recent_funding)
        if persistent_negative and pct_from_sma > -0.035 and rsi14 > 32.0:
            conf = min(0.9, 0.55 + abs(current_funding) * 1500)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=240.0,
                take_profit_bps=380.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "contrarian_short_squeeze",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "pct_from_sma": pct_from_sma,
                },
            )

        # Short squeeze condition: persistent positive funding, price not aggressively breaking out
        persistent_positive = all(f >= self.funding_threshold for f in recent_funding)
        if persistent_positive and pct_from_sma < 0.035 and rsi14 < 68.0:
            conf = min(0.9, 0.55 + current_funding * 1500)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=240.0,
                take_profit_bps=380.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "contrarian_long_unwind",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "pct_from_sma": pct_from_sma,
                },
            )

        return None