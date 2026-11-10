from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 2:
            return None

        # Filter extreme crisis environments to avoid cascading liquidation failures
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        # Cooldown guard: enforce post-exit and post-entry spacing to prevent overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Check existing position management
        if ctx.has_position():
            return None

        # Calculate Price Return over lookback
        price_ret = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        
        # Volatility check: price move must be meaningful (e.g. at least 1.0% over 24h)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        min_move_pct = max(0.010, (atr / closes[-1]) * 1.5) if atr > 0 else 0.010

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Funding rate threshold: typical neutral is ~0.0001 (0.01%).
        # Negative funding (< -0.00002) with rising price => trapped shorts squeeze
        # High positive funding (> 0.00020) with falling price => trapped longs flush
        is_short_squeeze = (price_ret > min_move_pct) and (funding < -0.00002)
        is_long_flush = (price_ret < -min_move_pct) and (funding > 0.00020)

        if is_short_squeeze:
            self.last_entry_bar = ctx.bar_index
            # Trapped shorts: dynamic confidence based on divergence intensity
            conf = min(0.85, 0.55 + abs(funding) * 1000 + (price_ret * 5))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_ret_24h": round(price_ret, 4),
                    "funding_rate": funding,
                    "close": closes[-1],
                    "min_move_pct": round(min_move_pct, 4)
                }
            )

        if is_long_flush:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.85, 0.55 + funding * 1000 + (abs(price_ret) * 5))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "long_liquidation_flush",
                    "price_ret_24h": round(price_ret, 4),
                    "funding_rate": funding,
                    "close": closes[-1],
                    "min_move_pct": round(min_move_pct, 4)
                }
            )

        return None