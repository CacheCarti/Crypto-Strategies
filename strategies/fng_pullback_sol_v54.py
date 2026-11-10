from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentPullbackCrossConfirm(Strategy):
    METADATA = {
        "name": "SolSentimentPullbackCrossConfirm",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        # Cross-asset filter & sentiment slow gate
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Base price structures
        current_price = ctx.bar.close
        highest_high = max(highs[-self.lookback:])
        lowest_low = min(lows[-self.lookback:])
        sma20 = self._sma(closes, 20)

        if sma20 is None:
            return None

        # Manage open position
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Mean-reversion or sentiment normalization exit
            if direction == "long":
                if current_price >= sma20 or sentiment >= 0.0 or bars_held >= 24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_norm",
                            "price": current_price,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "bars_held": bars_held,
                        },
                    )
            elif direction == "short":
                if current_price <= sma20 or sentiment <= 0.0 or bars_held >= 24:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_norm",
                            "price": current_price,
                            "sma20": sma20,
                            "sentiment": sentiment,
                            "bars_held": bars_held,
                        },
                    )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety: avoid entering against systemic liquidation / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Setup:
        # 1) Sentiment is fearful (< -0.3)
        # 2) SOL pulled back >= 3% from the 48-bar high
        # 3) Cross-asset confirmation: BTC 24h return stabilized (>= -1.5% to avoid falling knives)
        # 4) Short-term turning signal: close > open (intrabar bounce)
        pullback_from_high = (highest_high - current_price) / highest_high
        if sentiment < -0.3 and pullback_from_high >= self.pullback_pct:
            if btc_ret >= -1.5 and ctx.bar.close > ctx.bar.open:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=400.0,
                    take_profit_bps=650.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "fear_pullback_btc_stabilized",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high * 100.0,
                        "btc_return_pct": btc_ret,
                        "price": current_price,
                        "sma20": sma20,
                    },
                )

        # Short Setup:
        # 1) Sentiment is euphoric (> 0.3)
        # 2) SOL rallied >= 3% from the 48-bar low
        # 3) Cross-asset confirmation: BTC 24h return capped (<= 1.5% to avoid strong macro breakouts)
        # 4) Short-term turning signal: close < open (intrabar rejection)
        rally_from_low = (current_price - lowest_low) / lowest_low
        if sentiment > 0.3 and rally_from_low >= self.pullback_pct:
            if btc_ret <= 1.5 and ctx.bar.close < ctx.bar.open:
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=400.0,
                    take_profit_bps=650.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "greed_rally_btc_capped",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low * 100.0,
                        "btc_return_pct": btc_ret,
                        "price": current_price,
                        "sma20": sma20,
                    },
                )

        return None