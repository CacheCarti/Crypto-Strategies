from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceLong(Strategy):
    METADATA = {
        "name": "EthConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _rsi(self, closes, period=14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Feature readings
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        current_price = ctx.bar.close

        # Robust regime gate: avoid complete MELTDOWN / extreme CRISIS crashes where oversold keeps trending lower
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime_ok = market_regime not in ("MELTDOWN", "CRISIS") and crisis_score < 0.70

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Normalize exits: RSI recovery, sentiment flipping neutral/greed, or funding turning deeply positive
            rsi_recovered = rsi >= 52.0
            sentiment_recovered = fg_index >= 45.0
            funding_flipped_hot = funding_rate > 0.00015
            max_hold_exceeded = bars_held >= 16

            if rsi_recovered or sentiment_recovered or funding_flipped_hot or max_hold_exceeded:
                self.last_exit_bar = ctx.bar_index
                exit_reason = (
                    "rsi_normalized" if rsi_recovered else
                    "sentiment_normalized" if sentiment_recovered else
                    "funding_positive_exit" if funding_flipped_hot else
                    "max_hold_reached"
                )
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding_rate": round(funding_rate, 6),
                        "fear_greed": round(fg_index, 1),
                        "bars_held": bars_held,
                        "price": round(current_price, 2)
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry criteria: high-conviction confluence stack
        # 1. Negative funding (crowd shorting ETH)
        # 2. Market in fear state (fg_index <= 35.0)
        # 3. Oversold price action (RSI <= 35.0)
        # 4. Gated from structural crash/meltdown regimes
        edge_funding = funding_rate < 0.0
        edge_fear = fg_index <= 35.0
        edge_rsi = rsi <= 35.0

        if edge_funding and edge_fear and edge_rsi and regime_ok:
            self.entry_bar = ctx.bar_index
            # Scale confidence with the depth of the dislocation
            conf = 0.70
            if rsi < 28.0:
                conf += 0.10
            if funding_rate < -0.00005:
                conf += 0.10
            confidence = min(0.95, conf)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_short_squeeze_oversold",
                    "rsi": round(rsi, 2),
                    "funding_rate": round(funding_rate, 6),
                    "fear_greed": round(fg_index, 1),
                    "regime": market_regime,
                    "crisis_score": round(crisis_score, 3),
                    "price": round(current_price, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index