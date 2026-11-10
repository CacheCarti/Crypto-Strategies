from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Adaptive",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold for swing moves
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.funding_history = []
        self.max_hist = 200

    def _percentile_rank(self, values, current):
        if not values:
            return 50.0
        count = sum(1 for v in values if v < current)
        return (count / len(values)) * 100.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Track rolling funding rate for dynamic state / percentile assessment
        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_hist:
            self.funding_history.pop(0)

        # Enforce cooldown to avoid overtrading friction
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Price momentum over lookback
        price_start = closes[-self.lookback - 1]
        price_now = closes[-1]
        price_return_pct = ((price_now - price_start) / price_start) * 100.0

        # Funding regime via percentile ranking
        funding_pct = self._percentile_rank(self.funding_history, current_funding)

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion of trapped side or momentum fading
            if pos_dir == "long":
                if funding_pct > 75.0 or price_return_pct < -0.5:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "squeeze_exhaustion_long",
                            "price_ret": price_return_pct,
                            "funding_pct": funding_pct,
                        }
                    )
            elif pos_dir == "short":
                if funding_pct < 25.0 or price_return_pct > 0.5:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "squeeze_exhaustion_short",
                            "price_ret": price_return_pct,
                            "funding_pct": funding_pct,
                        }
                    )
            return None

        # Avoid entries in crisis/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # LONG SQUEEZE: Price rises significantly (+1.0%), but funding is negatively skewed (< 25th percentile)
        # Shorts are caught off-guard, fueling continuation squeeze
        if price_return_pct >= 1.0 and funding_pct <= 25.0 and current_funding <= 0.0:
            confidence = min(1.0, 0.6 + (price_return_pct / 10.0))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_ret_24h": round(price_return_pct, 2),
                    "funding_pct": round(funding_pct, 1),
                    "funding_rate": current_funding,
                    "market_regime": market_regime,
                }
            )

        # SHORT SQUEEZE (Long Trap): Price falls significantly (-1.0%), but funding remains elevated (> 75th percentile)
        # Over-leveraged longs are trapped, accelerating selloff
        if price_return_pct <= -1.0 and funding_pct >= 75.0 and current_funding >= 0.0001:
            confidence = min(1.0, 0.6 + (abs(price_return_pct) / 10.0))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_continuation",
                    "price_ret_24h": round(price_return_pct, 2),
                    "funding_pct": round(funding_pct, 1),
                    "funding_rate": current_funding,
                    "market_regime": market_regime,
                }
            )

        return None