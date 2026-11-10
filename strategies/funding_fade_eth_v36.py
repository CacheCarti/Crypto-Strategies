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
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_ema = 50
        self.period_rsi = 14
        self.funding_history = []
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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
        closes = ctx.closes(self.period_ema + 15)
        if len(closes) < self.period_ema + 15:
            return None

        # Track persistent funding history (hourly cadence)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 20:
            self.funding_history.pop(0)

        # Require funding history to establish persistence
        if len(self.funding_history) < 3:
            return None

        # Persistence check: mean of last 3 bars
        recent_funding = self.funding_history[-3:]
        avg_funding = sum(recent_funding) / 3.0

        current_price = ctx.bar.close
        ema50 = self._ema(closes, self.period_ema)
        rsi = self._rsi(closes, self.period_rsi)
        if ema50 is None or rsi is None:
            return None

        # Normalize exit logic: close position when funding returns near neutral
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and avg_funding >= -0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized_exit_long",
                        "avg_funding": avg_funding,
                        "rsi": rsi,
                        "price": current_price
                    }
                )
            elif pos_dir == "short" and avg_funding <= 0.00005:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized_exit_short",
                        "avg_funding": avg_funding,
                        "rsi": rsi,
                        "price": current_price
                    }
                )
            return None

        # Cooldown guard: wait after previous exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filter: avoid trading into acute crisis or extreme breakdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        pct_from_ema = (current_price - ema50) / ema50

        # High-conviction Short Squeeze Long setup:
        # 1. Negative funding persisted for 3 bars (crowd paying to short)
        # 2. Not falling off a cliff: price within 3.5% of EMA50 and RSI turning up from oversold (> 32)
        long_funding_extreme = all(f <= -0.00008 for f in recent_funding) and avg_funding <= -0.00012
        if long_funding_extreme and pct_from_ema > -0.035 and 32.0 <= rsi <= 55.0:
            conf = min(0.9, 0.65 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_short_squeeze_long",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "ema50": ema50,
                    "pct_from_ema": pct_from_ema
                }
            )

        # High-conviction Long Squeeze Short setup:
        # 1. Very positive funding persisted for 3 bars (over-leveraged long crowd)
        # 2. Not in a vertical breakout: price within 4% of EMA50 and RSI showing exhaustion (48-68)
        short_funding_extreme = all(f >= 0.00018 for f in recent_funding) and avg_funding >= 0.00022
        if short_funding_extreme and pct_from_ema < 0.04 and 48.0 <= rsi <= 68.0:
            conf = min(0.9, 0.65 + avg_funding * 800.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_long_squeeze_short",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "ema50": ema50,
                    "pct_from_ema": pct_from_ema
                }
            )

        return None