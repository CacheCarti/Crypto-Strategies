from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 72,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.max_history = 120
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.bars_in_pos = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_pos = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(70)
        if len(closes) < 70:
            return None

        # Track rolling funding rate
        fr = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(fr)
        if len(self.funding_history) > self.max_history:
            self.funding_history.pop(0)

        if len(self.funding_history) < 48:
            return None

        # Rolling percentiles of funding rate to avoid static threshold overfitting
        sorted_fr = sorted(self.funding_history)
        n = len(sorted_fr)
        p15 = sorted_fr[int(n * 0.15)]
        p85 = sorted_fr[int(n * 0.85)]
        fr_mean = sum(self.funding_history[-3:]) / 3.0  # require persistence over 3 bars

        # Trend filter & momentum
        sma50 = self._sma(closes, 50)
        rsi = self._rsi(closes, 14)
        if sma50 is None or rsi is None:
            return None

        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            self.bars_in_pos += 1
            direction = ctx.position_direction()
            # Normalize exit condition
            if direction == "long" and fr >= 0.0 and rsi > 52.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_long", "fr": fr, "rsi": rsi})
            elif direction == "short" and fr <= 0.0 and rsi < 48.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "funding_normalized_short", "fr": fr, "rsi": rsi})
            return None

        # Cooldown guard after recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter from engine
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Extreme crowd shorting -> Long squeeze thesis
        # Negative funding persistently low (at/below 15th percentile), price not in a freefall meltdown (RSI > 32 and near/above SMA50 * 0.96)
        if fr_mean <= p15 and fr_mean < -0.00005:
            if price > sma50 * 0.97 and rsi > 34.0:
                conf = min(0.85, 0.55 + abs(fr_mean) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=420.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "funding_squeeze_long",
                        "fr_mean": round(fr_mean, 6),
                        "p15": round(p15, 6),
                        "rsi": round(rsi, 2),
                        "price": price,
                    }
                )

        # Extreme crowd longing -> Short unwind thesis
        # Positive funding persistently elevated (at/above 85th percentile), price not in vertical parabolic spike
        if fr_mean >= p85 and fr_mean > 0.00008:
            if price < sma50 * 1.03 and rsi < 66.0:
                conf = min(0.85, 0.55 + fr_mean * 2000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=420.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "funding_fade_short",
                        "fr_mean": round(fr_mean, 6),
                        "p85": round(p85, 6),
                        "rsi": round(rsi, 2),
                        "price": price,
                    }
                )

        return None