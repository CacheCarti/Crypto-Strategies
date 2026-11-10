from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold for swing
        "warmup_bars": 35,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 12
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Fetch required platform features
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)
        regime = ctx.market.get("regime", "NORMAL")

        # Check existing position for exit logic
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Exit when RSI recovers above neutral or funding turns crowded positive
            if rsi >= 52.0 or funding > 0.00015 or bars_held >= 24:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "confluence_normalized_or_time_stop",
                        "rsi": round(rsi, 2),
                        "funding": funding,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis filter: avoid catching falling knives during complete market meltdowns
        if regime == "MELTDOWN":
            return None

        # Triple edge stack:
        # 1. Crowd leaning short (negative or flat funding)
        # 2. Macro/market fear (Fear & Greed <= 38 to allow robust trade generation)
        # 3. Technical oversold exhaustion (RSI < 36)
        is_funding_discount = funding <= 0.0
        is_fear = fg_index <= 38.0
        is_oversold = rsi <= 36.0

        if is_funding_discount and is_fear and is_oversold:
            # Scale confidence by depth of discount & fear
            conf = 0.65
            if funding < -0.00005:
                conf += 0.15
            if rsi < 30.0:
                conf += 0.10
            conf = min(conf, 0.95)

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "triple_confluence_short_squeeze_oversold",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fg_index,
                    "regime": regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index