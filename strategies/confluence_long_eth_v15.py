from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthConfluenceReversion(Strategy):
    METADATA = {
        "name": "EthConfluenceReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # 8 hours swing hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 20:
            return None

        # Check existing position for thesis normalization or time stop
        if ctx.has_position():
            rsi = self._rsi(closes, period=14)
            funding = ctx.features.get("funding_rate_ethusdt", 0.0)
            bars_held = ctx.bar_index - self.entry_bar

            # Exit when RSI normalizes above neutral (50) or funding flips significantly positive
            if rsi is not None and rsi >= 52.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_normalized", "rsi": rsi, "funding": funding, "bars_held": bars_held}
                )
            if funding > 0.00015:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized", "rsi": rsi, "funding": funding, "bars_held": bars_held}
                )
            if bars_held >= 12:  # Max 12h hold
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_reached", "bars_held": bars_held, "rsi": rsi}
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Feature checks
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Base confluence filters
        funding_crowd_short = funding <= 0.0
        sentiment_fear = fear_greed <= 38.0

        if not (funding_crowd_short and sentiment_fear):
            return None

        # Price indicator: RSI(14)
        rsi = self._rsi(closes, period=14)
        if rsi is None or rsi >= 38.0:
            return None

        # Volatility / cross-asset confirmation to break prior failure modes
        # Reject if BTC is in a free-fall crash (> 4% down in 24h) or market in meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or btc_ret < -4.0:
            return None

        # Entry trigger: Local price tick rebound (close > open or close > previous close)
        # Prevents catching falling knives into freefalls
        rebound = ctx.bar.close >= ctx.bar.open or closes[-1] > closes[-2]
        if not rebound:
            return None

        # Dynamic confidence based on extremity of fear & oversoldness
        confidence = 0.65
        if rsi < 30.0:
            confidence += 0.15
        if funding < -0.00005:
            confidence += 0.10
        confidence = min(1.0, confidence)

        self.entry_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=350.0,
            take_profit_bps=550.0,
            horizon_seconds=28800,
            metadata={
                "reason": "triple_stack_confluence_rebound",
                "rsi": rsi,
                "funding": funding,
                "fear_greed": fear_greed,
                "btc_ret": btc_ret,
                "regime": regime,
            }
        )