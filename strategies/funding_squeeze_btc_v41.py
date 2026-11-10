from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_trade_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 18

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Manage existing position horizon
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            rsi_val = self._rsi(closes, 14)
            pos_dir = ctx.position_direction()

            # Dynamic momentum exhaustion exit
            if pos_dir == "long" and rsi_val > 72.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_overbought_exit", "rsi": rsi_val})
            if pos_dir == "short" and rsi_val < 28.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_oversold_exit", "rsi": rsi_val})
            
            # Horizon exit to avoid capital tie-up
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_bars_exhaustion", "bars_held": bars_held})
            
            return None

        # Cooldown guard to avoid friction burnout
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Funding positioning vs Price direction divergence
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        price_change_pct = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        rsi = self._rsi(closes, 14)

        # Skip high crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Long Squeeze: Price actively advancing (>1.2%) while funding is negative/crowded short
        if price_change_pct >= 0.012 and funding_rate <= -0.00002 and rsi < 68.0:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=200.0,
                take_profit_bps=420.0,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding_rate": funding_rate,
                    "price_change_pct": round(price_change_pct, 4),
                    "rsi": round(rsi, 2)
                }
            )

        # Short Squeeze / Long Liquidation: Price breaking down (<-1.2%) while funding stays positive
        if price_change_pct <= -0.012 and funding_rate >= 0.00008 and rsi > 32.0:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=200.0,
                take_profit_bps=420.0,
                metadata={
                    "reason": "long_liquidation_continuation",
                    "funding_rate": funding_rate,
                    "price_change_pct": round(price_change_pct, 4),
                    "rsi": round(rsi, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index