from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "FundingSqueezeMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_trade_bar = -100
        self.entry_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Check existing position management
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            # Time-based active exit or flip check
            if bars_in_pos >= 18:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_based_take_profit_or_decay",
                        "bars_in_trade": bars_in_pos,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Respect cooldown between trades to avoid overtrading
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes where funding becomes heavily disconnected
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price momentum over lookback
        price_start = closes[-self.lookback - 1]
        price_now = closes[-1]
        price_change_pct = (price_now - price_start) / price_start

        # Funding rate reading
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # 1-bar price confirmation (momentum continuation confirmation)
        short_sma = self._sma(closes, 6)
        if short_sma is None:
            return None

        # Bullish Squeeze: Price actively advancing (>1.2%) while funding is negative (< -0.00005)
        # Shorts are trapped and fighting a rising market
        if price_change_pct > 0.012 and funding < -0.00005 and price_now > short_sma:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "short_squeeze_divergence_breakout",
                    "price_change_pct": round(price_change_pct, 4),
                    "funding_rate": funding,
                    "price": price_now,
                    "sma_fast": short_sma
                }
            )

        # Bearish Squeeze: Price actively declining (<-1.2%) while funding is overly positive (> 0.0001)
        # Longs are trapped paying premium while spot price degrades
        if price_change_pct < -0.012 and funding > 0.0001 and price_now < short_sma:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "long_liquidation_squeeze_divergence",
                    "price_change_pct": round(price_change_pct, 4),
                    "funding_rate": funding,
                    "price": price_now,
                    "sma_fast": short_sma
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index