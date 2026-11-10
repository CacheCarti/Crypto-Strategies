from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeContinuation(Strategy):
    METADATA = {
        "name": "FundingSqueezeContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.atr_period = 14

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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        highs = ctx.highs(self.atr_period + 2)
        lows = ctx.lows(self.atr_period + 2)
        c_atr = ctx.closes(self.atr_period + 2)
        atr = self._atr(highs, lows, c_atr, self.atr_period)
        if atr is None or ctx.bar.close <= 0:
            return None

        # Structural volatility-based risk limits
        atr_pct = atr / ctx.bar.close
        sl_bps = max(140.0, min(320.0, atr_pct * 1.6 * 10000.0))
        tp_bps = max(280.0, min(650.0, sl_bps * 2.1))

        current_price = ctx.bar.close
        past_price = closes[-self.lookback]
        price_change_pct = (current_price - past_price) / past_price

        # Check in-position dynamic / timed exit
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos.get("entry_price", current_price) if pos else current_price
            direction = ctx.position_direction()
            
            # Trailing structural/giveback exit
            if direction == "long":
                ret_bps = ((current_price - entry_price) / entry_price) * 10000.0
                if ret_bps > 180.0 and current_price < closes[-2]:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_momentum_fade", "ret_bps": ret_bps})
            elif direction == "short":
                ret_bps = ((entry_price - current_price) / entry_price) * 10000.0
                if ret_bps > 180.0 and current_price > closes[-2]:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_momentum_fade", "ret_bps": ret_bps})
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market stress gate: Avoid entering during extreme crisis liquidations
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Funding squeeze divergence logic:
        # 1. Price is rising significantly over 24h, but funding remains negative (shorts trapped into squeeze)
        # 2. Price is falling significantly over 24h, but funding remains positive (longs trapped into cascade)
        min_momentum_pct = 0.012

        if price_change_pct >= min_momentum_pct and funding < -0.00001:
            confidence = min(0.9, 0.6 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_squeeze_short_trap",
                    "funding_rate": funding,
                    "price_change_pct": round(price_change_pct, 4),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                },
            )

        if price_change_pct <= -min_momentum_pct and funding > 0.00001:
            confidence = min(0.9, 0.6 + abs(funding) * 500.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_squeeze_long_trap",
                    "funding_rate": funding,
                    "price_change_pct": round(price_change_pct, 4),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                },
            )

        return None