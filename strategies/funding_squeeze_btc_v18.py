from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeEventBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeEventBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold target
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 18
        self.last_exit_bar = -999
        self.min_return_bps = 70.0
        self.min_abs_funding = 0.00003  # 0.3 bps per 8h minimum positioning tilt

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Hard cooldown check after prior trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Check existing position management
        if ctx.has_position():
            return None

        # Market stress filter: bypass during extreme meltdown to avoid regime breaks
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Feature positioning
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        if abs(funding) < self.min_abs_funding:
            return None

        # Price momentum over event horizon (24 bars)
        current_close = closes[-1]
        past_close = closes[-self.lookback - 1]
        price_ret_bps = ((current_close - past_close) / past_close) * 10000.0

        # Divergence Detection:
        # Long Squeeze: Price is pushing UP strongly while funding is persistently NEGATIVE (trapped shorts)
        # Short Squeeze: Price is breaking DOWN strongly while funding is persistently POSITIVE (trapped longs)
        short_squeeze_long_signal = (price_ret_bps > self.min_return_bps) and (funding <= -self.min_abs_funding)
        long_liquidation_short_signal = (price_ret_bps < -self.min_return_bps) and (funding >= self.min_abs_funding)

        if not (short_squeeze_long_signal or long_liquidation_short_signal):
            return None

        # Volatility normalization for dynamic SL/TP
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or current_close == 0:
            atr_bps = 80.0
        else:
            atr_bps = (atr_val / current_close) * 10000.0

        # Scale stops relative to volatility but keep bounds
        sl_bps = max(160.0, min(300.0, atr_bps * 1.8))
        tp_bps = max(320.0, min(650.0, atr_bps * 3.6))

        # Dynamic confidence based on magnitude of divergence
        norm_funding = min(abs(funding) / 0.00015, 1.0)
        norm_ret = min(abs(price_ret_bps) / 250.0, 1.0)
        confidence = max(0.55, min(0.90, 0.5 + (0.25 * norm_funding) + (0.25 * norm_ret)))

        if short_squeeze_long_signal:
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=28800,
                metadata={
                    "reason": "short_squeeze_negative_funding_divergence",
                    "price_ret_bps": round(price_ret_bps, 1),
                    "funding_rate": round(funding, 6),
                    "atr_bps": round(atr_bps, 1),
                    "close": current_close,
                },
            )

        if long_liquidation_short_signal:
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=round(sl_bps, 1),
                take_profit_bps=round(tp_bps, 1),
                horizon_seconds=28800,
                metadata={
                    "reason": "long_liquidation_positive_funding_divergence",
                    "price_ret_bps": round(price_ret_bps, 1),
                    "funding_rate": round(funding, 6),
                    "atr_bps": round(atr_bps, 1),
                    "close": current_close,
                },
            )

        return None