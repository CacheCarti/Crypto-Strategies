from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold target
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.min_price_change_pct = 0.015  # 1.5% move over 24 bars
        self.funding_threshold = 0.00005   # 0.005% baseline bias

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
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

        # Cooldown guard to maintain 10-120 selective trades across backtest window
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: Avoid entering during severe crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": market_regime}
                )
            return None

        current_price = ctx.bar.close
        price_24h_ago = closes[-self.lookback]
        price_return = (current_price - price_24h_ago) / price_24h_ago

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        eth_return = ctx.features.get("eth_return_pct", 0.0)

        # Volatility state confirmation using ATR expansion relative to typical range
        highs = ctx.highs(28)
        lows = ctx.lows(28)
        current_atr = self._atr(highs, lows, closes, 14)
        base_atr = self._atr(highs[:14], lows[:14], closes[:14], 14)
        vol_expanding = current_atr > (base_atr * 1.05) if base_atr > 0 else True

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean-reverting funding decay exit: squeeze thesis resolves when funding normalizes
            if pos_dir == "long" and funding > self.funding_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "funding": funding, "price": current_price}
                )
            elif pos_dir == "short" and funding < -self.funding_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "funding": funding, "price": current_price}
                )
            return None

        # Divergence Setup 1: Short Squeeze Continuation
        # Price has pushed upwards strongly over 24h, but funding remains negative (shorts trapped).
        # Cross-asset validation: ETH is not completely collapsing (eth_return > -1.5%).
        # Volatility confirmation: ATR is expanding, confirming real flow rather than drift.
        long_divergence = (
            price_return >= self.min_price_change_pct
            and funding <= -self.funding_threshold
            and eth_return > -1.5
            and vol_expanding
        )

        # Divergence Setup 2: Long Squeeze (Cascading Liquidation) Continuation
        # Price dropped over 24h while funding remains high/positive (stubborn retail longs trapped).
        # Cross-asset confirmation: broad crypto weakness (eth_return < 1.5%).
        short_divergence = (
            price_return <= -self.min_price_change_pct
            and funding >= self.funding_threshold
            and eth_return < 1.5
            and vol_expanding
        )

        if long_divergence:
            confidence = min(1.0, 0.65 + abs(funding) * 500.0)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_funding_divergence",
                    "price_return_24h": round(price_return, 4),
                    "funding_rate": funding,
                    "eth_return": round(eth_return, 2),
                    "atr": round(current_atr, 2),
                }
            )

        if short_divergence:
            confidence = min(1.0, 0.65 + abs(funding) * 500.0)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_funding_divergence",
                    "price_return_24h": round(price_return, 4),
                    "funding_rate": funding,
                    "eth_return": round(eth_return, 2),
                    "atr": round(current_atr, 2),
                }
            )

        return None