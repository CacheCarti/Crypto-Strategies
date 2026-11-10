from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceSqueeze(Strategy):
    METADATA = {
        "name": "EthConfluenceSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Active position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Normalize exit: funding flipped heavily positive, RSI overbought, or held long enough
            exit_trigger = (
                rsi >= 60.0 or
                funding > 0.00015 or
                market_regime == "MELTDOWN" or
                bars_held >= 12
            )
            if exit_trigger:
                reason = "rsi_rebound" if rsi >= 60.0 else ("funding_rebound" if funding > 0.00015 else "timeout_or_meltdown")
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={"reason": reason, "rsi": rsi, "funding": funding, "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime Gate: avoid entering during active collapse/meltdown where multi-day cascades override sentiment/funding
        if market_regime in ["MELTDOWN", "CRISIS"] or crisis_score > 0.65:
            return None

        # Triple Confluence Entry
        # 1. Crowd shorting bias (funding < 0)
        # 2. General market fear (fear_greed < 35)
        # 3. Oversold price exhaustion (RSI < 35)
        is_funding_crowded_short = funding < 0.0
        is_fear_dominant = fear_greed < 35.0
        is_price_oversold = rsi < 35.0

        if is_funding_crowded_short and is_fear_dominant and is_price_oversold:
            # Scale confidence by depth of oversold/fear
            confidence = min(0.90, max(0.60, (40.0 - rsi) / 20.0 + (35.0 - fear_greed) / 70.0))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_short_squeeze",
                    "rsi": round(rsi, 2),
                    "funding_rate": round(funding, 6),
                    "fear_greed": round(fear_greed, 1),
                    "crisis_score": round(crisis_score, 2),
                    "regime": market_regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100