from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingSqueezeContrarian(Strategy):
    METADATA = {
        "name": "EthFundingSqueezeContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.funding_long_thresh = -0.00015   # -0.015% per 8h (crowd heavily short)
        self.funding_short_thresh = 0.00030   # +0.030% per 8h (crowd heavily long)
        self.funding_history = []

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
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Track funding history over the last 6 bars
        raw_fr = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(raw_fr)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        current_price = ctx.bar.close
        ema50 = self._ema(closes, 50)
        rsi14 = self._rsi(closes, 14)
        if ema50 is None or rsi14 is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Normalize funding exit condition
            if pos_dir == "long" and raw_fr > 0.00005:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_exit_long",
                    "funding_rate": raw_fr,
                    "price": current_price
                })
            elif pos_dir == "short" and raw_fr < -0.00005:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_exit_short",
                    "funding_rate": raw_fr,
                    "price": current_price
                })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.funding_history) < 4:
            return None

        # Persistence check: at least 3 of last 4 readings must be extreme
        recent_fr = self.funding_history[-4:]
        persistent_negative = sum(1 for fr in recent_fr if fr <= self.funding_long_thresh) >= 3
        persistent_positive = sum(1 for fr in recent_fr if fr >= self.funding_short_thresh) >= 3

        # Event Boundary: Trigger on local price momentum inflection to avoid catching falling knives
        # Long entry: persistent negative funding + price reclaim of previous close + not in strong freefall
        if persistent_negative:
            # Price bouncing off local low while funding remains heavily negative
            price_inflection_bull = (closes[-1] > closes[-2]) and (rsi14 > 32.0 and rsi14 < 58.0)
            trend_not_broken = current_price > (ema50 * 0.94)  # Do not catch extreme runaway crashes
            
            if price_inflection_bull and trend_not_broken:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=320.0,
                    take_profit_bps=520.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "short_crowd_squeeze_inflection",
                        "funding_rate": raw_fr,
                        "rsi14": round(rsi14, 2),
                        "ema50": round(ema50, 2),
                        "price": current_price
                    }
                )

        # Short entry: persistent positive funding + price rolling over + not in runaway bull parabolic
        if persistent_positive:
            price_inflection_bear = (closes[-1] < closes[-2]) and (rsi14 < 68.0 and rsi14 > 42.0)
            trend_not_parabolic = current_price < (ema50 * 1.06)

            if price_inflection_bear and trend_not_parabolic:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=320.0,
                    take_profit_bps=520.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "long_crowd_fade_inflection",
                        "funding_rate": raw_fr,
                        "rsi14": round(rsi14, 2),
                        "ema50": round(ema50, 2),
                        "price": current_price
                    }
                )

        return None