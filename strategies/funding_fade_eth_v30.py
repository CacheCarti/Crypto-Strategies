from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingReversion(Strategy):
    METADATA = {
        "name": "EthFundingReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 72,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.max_history = 120
        self.cooldown_bars = 6
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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(72)
        if len(closes) < 72:
            return None

        # Track rolling funding rate distribution
        f_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(f_rate)
        if len(self.funding_history) > self.max_history:
            self.funding_history.pop(0)

        if len(self.funding_history) < 48:
            return None

        # Check persistence over last 3 bars
        recent_funding = self.funding_history[-3:]
        persisting_neg = all(f < 0.0 for f in recent_funding)
        persisting_pos = all(f > 0.0 for f in recent_funding)

        # Percentile rank of current funding in recent window
        sorted_f = sorted(self.funding_history)
        n = len(sorted_f)
        rank = sum(1 for x in sorted_f if x <= f_rate) / n

        # Indicators
        rsi = self._rsi(closes, period=14)
        ema24 = self._ema(closes, period=24)
        ema72 = self._ema(closes, period=72)

        if rsi is None or ema24 is None or ema72 is None:
            return None

        curr_price = ctx.bar.close

        # Position management: exit if funding normalizes to neutral bounds (35th-65th percentile)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and (rank >= 0.45 or rsi > 68):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_or_overbought", "rank": rank, "rsi": rsi}
                )
            elif pos_dir == "short" and (rank <= 0.55 or rsi < 32):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_or_oversold", "rank": rank, "rsi": rsi}
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis filter: don't catch knife in meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long Entry: Crowd paying heavy shorts (bottom 12% funding), persisting negative,
        # price not in runaway freefall (RSI > 25 and not excessively below slow EMA)
        if rank <= 0.12 and persisting_neg:
            not_freefall = (curr_price > ema72 * 0.94) and (rsi > 25) and (rsi < 48)
            if not_freefall:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=450.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "funding_squeeze_long",
                        "rank": rank,
                        "f_rate": f_rate,
                        "rsi": rsi,
                        "price": curr_price
                    }
                )

        # Short Entry: Crowd overly levered long (top 12% funding), persisting positive,
        # price not in parabolic blow-off (RSI < 75 and not excessively above slow EMA)
        if rank >= 0.88 and persisting_pos:
            not_blowoff = (curr_price < ema72 * 1.06) and (rsi < 75) and (rsi > 52)
            if not_blowoff:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=280.0,
                    take_profit_bps=450.0,
                    horizon_seconds=18000,
                    metadata={
                        "reason": "funding_exhaustion_short",
                        "rank": rank,
                        "f_rate": f_rate,
                        "rsi": rsi,
                        "price": curr_price
                    }
                )

        return None