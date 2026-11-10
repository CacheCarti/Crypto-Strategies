from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BtcFundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.atr_period = 14

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
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        price_24h_ago = closes[-self.lookback]
        btc_24h_ret_pct = ((current_price - price_24h_ago) / price_24h_ago) * 100.0

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        eth_ret = ctx.features.get("eth_return_pct", 0.0)

        # Volatility state confirmation via ATR
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or current_price == 0:
            return None
        atr_pct = (atr / current_price) * 100.0

        # Require minimum active volatility to ensure squeezes have follow-through
        if atr_pct < 0.35:
            return None

        # Market regime filter: avoid executing inside meltdown/crisis disorder
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Dynamic thesis invalidation
            if pos_dir == "long" and funding > 0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "long_funding_normalized_or_overcrowded", "funding": funding, "price": current_price}
                )
            if pos_dir == "short" and funding < -0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "short_funding_normalized_or_squeezed_out", "funding": funding, "price": current_price}
                )
            return None

        # Short squeeze thesis: Price appreciating noticeably (+1.2%) while funding is negative (< -0.00003),
        # confirmed by broad crypto market strength (ETH positive) to avoid isolated fakeouts.
        if btc_24h_ret_pct >= 1.2 and funding <= -0.00003 and eth_ret > 0.5:
            conf = min(0.9, 0.55 + (abs(funding) * 1000.0) + (btc_24h_ret_pct * 0.04))
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=520.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "btc_24h_ret_pct": round(btc_24h_ret_pct, 2),
                    "funding_rate": funding,
                    "eth_ret_pct": round(eth_ret, 2),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        # Long squeeze thesis: Price declining noticeably (-1.2%) while funding is stubbornly positive (> +0.00008),
        # confirmed by broad market weakness (ETH negative).
        if btc_24h_ret_pct <= -1.2 and funding >= 0.00008 and eth_ret < -0.5:
            conf = min(0.9, 0.55 + (funding * 800.0) + (abs(btc_24h_ret_pct) * 0.04))
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=520.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "long_liquidation_continuation",
                    "btc_24h_ret_pct": round(btc_24h_ret_pct, 2),
                    "funding_rate": funding,
                    "eth_ret_pct": round(eth_ret, 2),
                    "atr_pct": round(atr_pct, 3),
                }
            )

        return None