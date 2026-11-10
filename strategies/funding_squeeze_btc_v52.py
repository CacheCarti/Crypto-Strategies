from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.funding_history = []
        self.max_funding_hist = 30

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track recent funding rate to ensure sustained bias, not a single-tick spike
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > self.max_funding_hist:
            self.funding_history.pop(0)

        # Skip during active position or cooldown window
        if ctx.has_position():
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter: Avoid Meltdown / Crisis regime where squeezes fail due to cascading illiquidity
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Regime Gate: Edge is strongest when trend regime supports momentum continuation
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # 24-bar price return (%)
        p_now = closes[-1]
        p_prev = closes[-self.lookback]
        if p_prev <= 0:
            return None
        price_ret_24h = ((p_now - p_prev) / p_prev) * 100.0

        # Mean funding over the last 12 bars (sustained positioning bias)
        funding_window = self.funding_history[-12:]
        if len(funding_window) < 12:
            return None
        avg_funding = sum(funding_window) / len(funding_window)

        # Baseline trend filter via EMA 30
        ema30 = self._ema(closes, 30)
        if ema30 is None:
            return None

        # 1. Short Squeeze (Shorts Trapped):
        # Price has appreciated strongly (> 1.2%) over 24h, trading above EMA30,
        # but persistent funding is negative (<= -0.00005 per 8h), signaling aggressive short positioning.
        if (price_ret_24h > 1.2 and 
            p_now > ema30 and 
            avg_funding <= -0.00005 and 
            trend_regime in ("bull", "neutral")):

            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "short_squeeze_continuation_negative_funding",
                    "price_ret_24h": round(price_ret_24h, 3),
                    "avg_funding": round(avg_funding, 6),
                    "current_price": round(p_now, 2),
                    "ema30": round(ema30, 2),
                    "trend_regime": trend_regime,
                }
            )

        # 2. Long Squeeze (Longs Trapped):
        # Price has fallen substantially (< -1.2%) over 24h, trading below EMA30,
        # while persistent funding remains positive (>= +0.00015 per 8h), signaling stubborn longs.
        if (price_ret_24h < -1.2 and 
            p_now < ema30 and 
            avg_funding >= 0.00015 and 
            trend_regime in ("bear", "neutral")):

            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "long_liquidation_squeeze_positive_funding",
                    "price_ret_24h": round(price_ret_24h, 3),
                    "avg_funding": round(avg_funding, 6),
                    "current_price": round(p_now, 2),
                    "ema30": round(ema30, 2),
                    "trend_regime": trend_regime,
                }
            )

        return None