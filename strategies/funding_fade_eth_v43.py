from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,  # 4 hours intended avg hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.funding_lookback = 3
        # Baseline funding threshold: 0.01% (0.00010) per 8h
        self.funding_thresh = 0.00010

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Persistence check
        if len(self.funding_history) < self.funding_lookback:
            return None

        recent_funding = self.funding_history[-self.funding_lookback:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        # Exit logic if already positioned
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If funding normalizes or flips against our thesis, exit
            if pos_dir == "long" and avg_funding > 0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_exit_long", "avg_funding": avg_funding}
                )
            elif pos_dir == "short" and avg_funding < -0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_exit_short", "avg_funding": avg_funding}
                )
            return None

        # Mandatory cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: Prior evidence shows fading runs fail during crisis/meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.5:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price indicators to prevent fading against parabolic runaway trends
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        close = ctx.bar.close

        # Long Squeeze Setup:
        # Crowd is heavily short (paying funding < -thresh), persisting over last 3 bars.
        # Price shouldn't be completely collapsing below RSI 28, but showing a local stabilization.
        if (all(f <= -self.funding_thresh for f in recent_funding) and
            avg_funding <= -self.funding_thresh and
            rsi14 >= 30.0 and close > ema20 * 0.985):

            confidence = min(0.85, 0.5 + abs(avg_funding) * 1500)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "crowded_short_funding_squeeze",
                    "avg_funding": avg_funding,
                    "rsi": rsi14,
                    "close": close,
                    "ema20": ema20
                }
            )

        # Short Squeeze/Reversion Setup:
        # Crowd is over-leveraged long (paying funding > thresh), persisting over last 3 bars.
        # Price is not in a parabolic breakout (RSI <= 70), avoiding runaway squeezes against us.
        if (all(f >= self.funding_thresh for f in recent_funding) and
            avg_funding >= self.funding_thresh and
            rsi14 <= 70.0 and close < ema20 * 1.015):

            confidence = min(0.85, 0.5 + abs(avg_funding) * 1500)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "crowded_long_funding_fade",
                    "avg_funding": avg_funding,
                    "rsi": rsi14,
                    "close": close,
                    "ema20": ema20
                }
            )

        return None