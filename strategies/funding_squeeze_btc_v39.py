from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Adaptive",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 48,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 8
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.funding_history = []
        self.max_history = 120

    def _percentile(self, values, val):
        if not values:
            return 0.5
        count = sum(1 for v in values if v <= val)
        return count / len(values)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Track rolling funding rate history to build adaptive distribution
        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_history:
            self.funding_history.pop(0)

        # Skip during crisis or extreme meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": regime})
            return None

        # Exit management: time horizon exit if stalled
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= 24:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_holding_bars_reached", "bars_held": bars_in_trade}
                )
            return None

        # Mandatory cooldown after trade exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Price momentum over lookback (24 bars)
        price_ret = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        
        # Funding rate quantile rank (0.0 to 1.0)
        funding_pct = self._percentile(self.funding_history, current_funding)

        # Compute price volatility baseline to ensure meaningful price movement
        returns = [abs(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        avg_vol = sum(returns) / len(returns) if returns else 0.005
        min_move = max(0.008, avg_vol * math.sqrt(self.lookback) * 0.75)

        # Condition 1: Short Squeeze (Price rises solidly while funding is historically low/negative)
        # funding_pct <= 0.25 (lower quartile) and absolute funding <= 0.00002
        if price_ret > min_move and funding_pct <= 0.25 and current_funding <= 0.00002:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (price_ret / min_move) * 0.15)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_ret_24h": round(price_ret, 4),
                    "funding_pct": round(funding_pct, 3),
                    "funding_rate": current_funding,
                    "min_move_req": round(min_move, 4)
                }
            )

        # Condition 2: Long Squeeze (Price falls solidly while funding is historically crowded long)
        # funding_pct >= 0.75 (upper quartile) and absolute funding >= 0.0001
        if price_ret < -min_move and funding_pct >= 0.75 and current_funding >= 0.0001:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (abs(price_ret) / min_move) * 0.15)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "long_squeeze_liquidation",
                    "price_ret_24h": round(price_ret, 4),
                    "funding_pct": round(funding_pct, 3),
                    "funding_rate": current_funding,
                    "min_move_req": round(min_move, 4)
                }
            )

        return None