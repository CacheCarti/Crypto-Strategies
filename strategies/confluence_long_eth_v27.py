from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fgi = ctx.features.get("fear_greed_index", 50.0)

        # Active position management: exit when any leg strongly normalizes
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit triggers: RSI reached overbought/rebound, or funding flips heavily positive, or max hold reached
            if rsi >= 60.0 or funding > 0.00015 or bars_held >= 24:
                reason = "rsi_rebound" if rsi >= 60.0 else ("funding_flipped_long" if funding > 0.00015 else "max_hold_expired")
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "rsi": round(rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                    }
                )
            return None

        # Cooldown guard: require spacing between trades to prevent over-trading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry criteria: Triple Confluence Long
        # 1. Negative funding (crowd shorting perp)
        # 2. Fear & Greed < 38 (broader market fear/pessimism)
        # 3. RSI < 35 (oversold local momentum)
        if funding < 0.0 and fgi < 38.0 and rsi < 35.0:
            # Scale confidence by depth of oversold & fear
            conf = min(0.9, 0.5 + (35.0 - rsi) * 0.015 + max(0.0, -funding * 500.0))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(rsi, 2),
                    "fear_greed": round(fgi, 1),
                    "funding_rate": round(funding, 6),
                    "price": ctx.bar.close,
                }
            )

        return None