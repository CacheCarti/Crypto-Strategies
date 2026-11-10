from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingSqueezeFader(Strategy):
    METADATA = {
        "name": "EthFundingSqueezeFader",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_hist = []
        self.cooldown_until = 0
        self.min_cooldown_bars = 8
        self.extreme_neg_threshold = -0.00015  # -0.015% per 8h
        self.extreme_pos_threshold = 0.00035   # +0.035% per 8h
        self.neutral_threshold = 0.00005

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
        self.cooldown_until = ctx.bar_index + self.min_cooldown_bars

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_hist.append(current_funding)
        if len(self.funding_hist) > 10:
            self.funding_hist.pop(0)

        # In-position management: normalisation exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_funding >= -self.neutral_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_normalized_exit_long",
                        "funding": current_funding,
                        "close": ctx.bar.close,
                    },
                )
            elif pos_dir == "short" and current_funding <= self.neutral_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_normalized_exit_short",
                        "funding": current_funding,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Post-exit cooldown gate
        if ctx.bar_index < self.cooldown_until:
            return None

        if len(self.funding_hist) < 4:
            return None

        # Check funding persistence (last 3 bars stay extreme)
        recent_3 = self.funding_hist[-3:]
        is_crowd_short = all(f <= self.extreme_neg_threshold for f in recent_3)
        is_crowd_long = all(f >= self.extreme_pos_threshold for f in recent_3)

        if not (is_crowd_short or is_crowd_long):
            return None

        # Technical structure: trend filter via EMA40 and momentum turn via RSI14
        ema40 = self._ema(closes, 40)
        rsi = self._rsi(closes, 14)
        if ema40 is None or rsi is None:
            return None

        price = ctx.bar.close

        # Event trigger: crowd is deeply short, but price is consolidating/reversing (RSI turning out of oversold, not in a waterfall crash)
        # We fade into a squeeze when price hasn't fallen below 3% of EMA40 (avoid falling knives) and RSI holds above 32
        if is_crowd_short:
            dist_from_ema = (price - ema40) / ema40
            if -0.035 <= dist_from_ema <= 0.015 and 32.0 <= rsi <= 55.0:
                conf = min(0.9, 0.6 + abs(current_funding) * 1000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=320.0,
                    take_profit_bps=480.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "short_squeeze_funding_fade",
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "dist_ema_pct": round(dist_from_ema * 100, 2),
                        "price": price,
                    },
                )

        # Event trigger: crowd is heavily long/overleveraged, fade near/above EMA when momentum stalls (RSI 48-68)
        if is_crowd_long:
            dist_from_ema = (price - ema40) / ema40
            if -0.015 <= dist_from_ema <= 0.035 and 45.0 <= rsi <= 68.0:
                conf = min(0.9, 0.6 + current_funding * 1000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=320.0,
                    take_profit_bps=480.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "long_exhaustion_funding_fade",
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "dist_ema_pct": round(dist_from_ema * 100, 2),
                        "price": price,
                    },
                )

        return None