from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.funding_threshold = 0.00015
        self.norm_threshold = 0.00004
        self.persistence_bars = 3
        self.atr_period = 14
        self.ema_trend_period = 40

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        fr = float(ctx.features.get("funding_rate_ethusdt", 0.0))
        self.funding_history.append(fr)
        if len(self.funding_history) > 20:
            self.funding_history.pop(0)

        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Position exit handling based on funding normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and fr >= -self.norm_threshold:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_long_exit",
                    "funding_rate": fr,
                    "close": ctx.bar.close
                })
            elif pos_dir == "short" and fr <= self.norm_threshold:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_short_exit",
                    "funding_rate": fr,
                    "close": ctx.bar.close
                })
            return None

        # Funding persistence check
        if len(self.funding_history) < self.persistence_bars:
            return None

        recent_funding = self.funding_history[-self.persistence_bars:]
        persistent_negative = all(f <= -self.funding_threshold for f in recent_funding)
        persistent_positive = all(f >= self.funding_threshold for f in recent_funding)

        if not persistent_negative and not persistent_positive:
            return None

        # Regime & trend protection
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        ema_trend = self._ema(closes, self.ema_trend_period)
        if ema_trend is None:
            return None

        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0.0:
            return None

        curr_close = ctx.bar.close
        # Volatility-based adaptive stop and target
        vol_stop_bps = max(180.0, min(350.0, (atr * 2.2 / curr_close) * 10000.0))
        vol_tp_bps = vol_stop_bps * 1.5

        # Anti-trend runaway gate
        # Don't long if price is in freefall > 3.0 ATR below EMA
        # Don't short if price is parabolic > 3.0 ATR above EMA
        dist_from_ema = (curr_close - ema_trend) / atr

        if persistent_negative and dist_from_ema > -2.8:
            conf = min(0.9, 0.6 + abs(fr) * 1000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=vol_stop_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_negative_funding_squeeze",
                    "funding_rate": fr,
                    "dist_from_ema": dist_from_ema,
                    "atr": atr,
                    "price": curr_close
                }
            )

        if persistent_positive and dist_from_ema < 2.8:
            conf = min(0.9, 0.6 + abs(fr) * 1000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=vol_stop_bps,
                take_profit_bps=vol_tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "persistent_positive_funding_fade",
                    "funding_rate": fr,
                    "dist_from_ema": dist_from_ema,
                    "atr": atr,
                    "price": curr_close
                }
            )

        return None