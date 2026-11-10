from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # 6 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -50
        self.entry_bar = -1

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Normalization exit conditions: RSI recovers back to neutral/overbought or funding flips heavy positive
            funding = ctx.features.get("funding_rate_ethusdt", 0.0)
            if current_rsi >= 55.0 or funding > 0.00015 or bars_held >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "normalization_or_time_exit",
                        "rsi": round(current_rsi, 2),
                        "funding": funding,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Check post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Macro fear and funding sentiment
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        
        # Cross-asset confirmation: BTC not in free-fall panic (addresses prior family failure)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Triple edge stack:
        # 1. Price oversold (RSI <= 36.0)
        # 2. Crowd overly bearish (funding <= 0.0)
        # 3. Macro sentiment in fear (fear_greed <= 38.0)
        # Confirmation filter: BTC 24h return stabilized > -4.5% (avoid entering falling knives)
        is_oversold = current_rsi <= 36.0
        is_crowd_short = funding <= 0.0
        is_fear = fear_greed <= 38.0
        btc_stable = btc_ret > -4.5

        if is_oversold and is_crowd_short and is_fear and btc_stable:
            self.entry_bar = ctx.bar_index
            confidence = 0.65
            if funding < -0.00005:
                confidence += 0.15
            if fear_greed < 25.0:
                confidence += 0.10
            confidence = min(confidence, 0.95)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_confluence_rsi_funding_fear",
                    "rsi": round(current_rsi, 2),
                    "funding_rate": funding,
                    "fear_greed": fear_greed,
                    "btc_return_pct": btc_ret,
                }
            )

        return None