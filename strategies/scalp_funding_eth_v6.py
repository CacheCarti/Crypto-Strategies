from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingTiltedRsiScalp(Strategy):
    METADATA = {
        "name": "Funding Tilted RSI Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 9
        self.rsi_oversold = 20.0
        self.rsi_overbought = 80.0
        self.rsi_exit_long = 54.0
        self.rsi_exit_short = 46.0
        
        # Funding tilt thresholds (meaningful skew, not near-zero noise)
        self.funding_long_thresh = -0.00002
        self.funding_short_thresh = 0.00008
        
        # Multi-bar cooldown to prevent rapid churn & bleed
        self.cooldown_bars = 42
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _rsi(self, closes: list, period: int) -> Optional[float]:
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
        # Filter out extreme crisis regimes where funding spreads detach
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_emergency_flat", "regime": regime},
                )
            return None

        closes = ctx.closes(self.rsi_period + 10)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi >= self.rsi_exit_long:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_reversion_long_exit",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "price": current_price,
                    },
                )
            elif pos_dir == "short" and rsi <= self.rsi_exit_short:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "rsi_reversion_short_exit",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "price": current_price,
                    },
                )
            return None

        # Hard multi-bar cooldown after previous exit and entry
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Long Setup: Distinctly negative funding (shorts paying) + severe oversold dip
        if funding <= self.funding_long_thresh and rsi <= self.rsi_oversold:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.95, 0.60 + (self.rsi_oversold - rsi) * 0.02)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "negative_funding_extreme_oversold_scalp",
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "price": current_price,
                },
            )

        # Short Setup: Crowded positive funding (longs paying) + severe overbought spike
        if funding >= self.funding_short_thresh and rsi >= self.rsi_overbought:
            self.last_entry_bar = ctx.bar_index
            conf = min(0.95, 0.60 + (rsi - self.rsi_overbought) * 0.02)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "positive_funding_extreme_overbought_scalp",
                    "rsi": round(rsi, 2),
                    "funding": funding,
                    "price": current_price,
                },
            )

        return None