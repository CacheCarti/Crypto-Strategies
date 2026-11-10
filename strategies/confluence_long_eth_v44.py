from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthTripleConfluenceLong(Strategy):
    METADATA = {
        "name": "EthTripleConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
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
        closes = ctx.closes(self.rsi_period + 2)
        if len(closes) < self.rsi_period + 1:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # In-position management
        if ctx.has_position():
            # Profit take or normalization exit
            bars_held = ctx.bar_index - self.entry_bar
            rsi_normalized = current_rsi >= 58.0
            funding_reset = funding > 0.0001
            time_stop = bars_held >= 14

            if rsi_normalized or (funding_reset and bars_held >= 4) or time_stop:
                reason = "rsi_normalized" if rsi_normalized else ("funding_positive" if funding_reset else "time_limit")
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": f"exit_{reason}",
                        "rsi": round(current_rsi, 2),
                        "funding": round(funding, 6),
                        "bars_held": bars_held,
                    }
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: Confluence of crowd shorting, market fear, and oversold momentum
        # Kept slightly broader than raw extreme to allow robust sample of 30-70 trades across 17k bars
        cond_funding = funding <= -0.000005  # Negative or heavily neutral-skewed perp funding
        cond_fg = fg_index <= 38.0           # Broad fear zone
        cond_rsi = current_rsi <= 36.0       # Oversold dip

        # Require price drop on the current candle to avoid catching falling knives prematurely
        price_recovering = ctx.bar.close >= ctx.bar.open or closes[-1] > closes[-2]

        if cond_funding and cond_fg and cond_rsi and price_recovering:
            # Scale confidence by depth of fear/funding negativity
            fear_score = min(max((38.0 - fg_index) / 25.0, 0.0), 1.0)
            rsi_score = min(max((36.0 - current_rsi) / 15.0, 0.0), 1.0)
            confidence = 0.55 + 0.2 * fear_score + 0.25 * rsi_score
            confidence = min(round(confidence, 2), 0.95)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_funding_fear_rsi",
                    "rsi": round(current_rsi, 2),
                    "funding": round(funding, 6),
                    "fear_greed": round(fg_index, 1),
                    "confidence": confidence,
                }
            )

        return None