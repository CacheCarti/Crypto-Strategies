from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.min_bars_held = 3

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
        closes = ctx.closes(45)
        if len(closes) < 40:
            return None

        # Track funding rate persistence (hourly rate, e.g. 0.0001 = 0.01%)
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Skip during meltdown / heavy crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": regime})
            return None

        rsi = self._rsi(closes, 14)
        ema20 = self._ema(closes, 20)
        ema40 = self._ema(closes, 40)
        if rsi is None or ema20 is None or ema40 is None:
            return None

        current_close = closes[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Persistence: average of the last 3 funding readings
        recent_funding = self.funding_history[-3:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        # 1. POSITION MANAGEMENT & EXIT
        if ctx.has_position():
            direction = ctx.position_direction()
            
            # Normalization exit: crowd positioning is no longer extreme
            if direction == "long" and avg_funding >= -0.00002:
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "exit_funding_normalized_long",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "price": current_close
                })
            elif direction == "short" and avg_funding <= 0.00003:
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "exit_funding_normalized_short",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "price": current_close
                })

            # Overextension counter-reversion technical exits
            if direction == "long" and rsi > 70.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "exit_rsi_overbought_long", "rsi": rsi})
            if direction == "short" and rsi < 30.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "exit_rsi_oversold_short", "rsi": rsi})

            return None

        # 2. ENTRY LOGIC (Only if flat and cooldown has passed)
        if bars_since_exit < self.cooldown_bars:
            return None

        if len(recent_funding) < 3:
            return None

        # Short squeeze candidate: crowd heavily short (negative funding for 3+ bars)
        # Price must NOT be plunging into freefall (trend filter: not collapsing severely below EMA40)
        is_funding_extremely_negative = all(f <= -0.00015 for f in recent_funding)
        trend_allows_long = current_close > (ema40 * 0.96) and rsi >= 30.0

        if is_funding_extremely_negative and trend_allows_long:
            conf = min(0.9, 0.65 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "short_squeeze_contrarian_entry",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": current_close
                }
            )

        # Long squeeze candidate: crowd overleveraged long (positive funding for 3+ bars)
        # Price must NOT be in a parabolic vertical runaway
        is_funding_extremely_positive = all(f >= 0.00025 for f in recent_funding)
        trend_allows_short = current_close < (ema40 * 1.04) and rsi <= 70.0

        if is_funding_extremely_positive and trend_allows_short:
            conf = min(0.9, 0.65 + avg_funding * 800.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "long_liquidation_contrarian_entry",
                    "avg_funding": avg_funding,
                    "rsi": rsi,
                    "ema20": ema20,
                    "price": current_close
                }
            )

        return None