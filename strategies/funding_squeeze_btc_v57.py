from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeBtc(Strategy):
    METADATA = {
        "name": "FundingSqueezeBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # 12-hour nominal swing hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.funding_history = []

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, len(closes))
        ]
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > self.lookback:
            self.funding_history.pop(0)

        closes = ctx.closes(self.lookback + 1)
        if len(closes) < self.lookback + 1 or len(self.funding_history) < self.lookback:
            return None

        # Cooldown guard to avoid overtrading and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in extreme panic/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_exit_crisis"})
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, ctx.closes(20), period=14)
        if atr is None or atr <= 0:
            return None

        current_close = closes[-1]
        past_close = closes[0]
        price_return_bps = ((current_close - past_close) / past_close) * 10000.0

        # Mean funding positioning across the last 24 bars
        avg_funding = sum(self.funding_history) / len(self.funding_history)

        # Dynamic volatility-based stop structure
        vol_sl_bps = max(180.0, min(380.0, (atr * 2.2 / current_close) * 10000.0))
        vol_tp_bps = vol_sl_bps * 2.0

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_close) if ctx.position else current_close
            pnl_bps = ((current_close - entry_price) / entry_price * 10000.0) if pos_dir == "long" else ((entry_price - current_close) / entry_price * 10000.0)

            # Squeeze exhaustion / thesis violation exit
            if pos_dir == "long" and avg_funding > 0.0001:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_normalized_long_exit", "avg_funding": avg_funding, "pnl_bps": pnl_bps})
            elif pos_dir == "short" and avg_funding < -0.0001:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "funding_normalized_short_exit", "avg_funding": avg_funding, "pnl_bps": pnl_bps})
            return None

        # Entry logic: True positioning vs price divergence
        # Long Squeeze: Price actively advancing (> 1.2%) while average funding is persistently negative (< -0.00003)
        if price_return_bps > 120.0 and avg_funding < -0.00003:
            confidence = min(0.90, 0.60 + abs(avg_funding) * 1500.0)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=round(vol_sl_bps, 1),
                take_profit_bps=round(vol_tp_bps, 1),
                horizon_seconds=43200,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_return_bps": round(price_return_bps, 1),
                    "avg_funding": round(avg_funding, 6),
                    "atr": round(atr, 2),
                },
            )

        # Short Squeeze: Price tumbling (< -1.2%) while average funding is persistently positive (> +0.00008)
        if price_return_bps < -120.0 and avg_funding > 0.00008:
            confidence = min(0.90, 0.60 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=round(vol_sl_bps, 1),
                take_profit_bps=round(vol_tp_bps, 1),
                horizon_seconds=43200,
                metadata={
                    "reason": "long_liquidation_continuation",
                    "price_return_bps": round(price_return_bps, 1),
                    "avg_funding": round(avg_funding, 6),
                    "atr": round(atr, 2),
                },
            )

        return None