from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Regime Gate",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.min_ret_threshold = 0.015
        self.funding_long_thresh = -0.00005
        self.funding_short_thresh = 0.00010

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 20:
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes[-20:], period=14)
        if atr is None:
            return None

        # Trend & structural filter via 50 EMA equivalent on closes
        ema_trend = self._ema(closes, 30)
        if ema_trend is None:
            return None

        # Market regime gate: avoid crisis / meltdown to prevent massive slippage / unanchored divergence
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        pct_change = (current_close - past_close) / past_close
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Check existing position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # If trapped conditions flip significantly, flatten early
            if pos_dir == "long" and (funding > 0.0002 or current_close < ema_trend - 2.0 * atr):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_thesis_invalidated_or_flipped", "funding": funding, "close": current_close}
                )
            elif pos_dir == "short" and (funding < -0.0001 or current_close > ema_trend + 2.0 * atr):
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_thesis_invalidated_or_flipped", "funding": funding, "close": current_close}
                )
            return None

        # Short squeeze scenario: Price surging despite persistently negative/flat funding (shorts heavily trapped)
        if pct_change >= self.min_ret_threshold and funding <= self.funding_long_thresh:
            if current_close > ema_trend:
                confidence = min(0.9, 0.6 + abs(funding) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_squeeze_divergence",
                        "price_return_24h": round(pct_change, 4),
                        "funding_rate": funding,
                        "close": current_close,
                        "ema": round(ema_trend, 2),
                    }
                )

        # Long squeeze scenario: Price sinking despite excessive positive funding (overleveraged longs trapped)
        if pct_change <= -self.min_ret_threshold and funding >= self.funding_short_thresh:
            if current_close < ema_trend:
                confidence = min(0.9, 0.6 + funding * 2000.0)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_squeeze_divergence",
                        "price_return_24h": round(pct_change, 4),
                        "funding_rate": funding,
                        "close": current_close,
                        "ema": round(ema_trend, 2),
                    }
                )

        return None