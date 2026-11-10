from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluenceLong(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 20:
            return None

        # Position tracking & management
        if ctx.has_position():
            self.bars_in_trade += 1
            curr_rsi = self._rsi(closes, period=14)
            funding = ctx.features.get("funding_rate_ethusdt", 0.0)

            # Normalization exit or hold-time exit
            if curr_rsi is not None and curr_rsi >= 55.0:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "rsi_mean_reversion_achieved",
                    "rsi": curr_rsi,
                    "bars_in_trade": self.bars_in_trade
                })

            if funding > 0.0001:  # Longs paying meaningful premium
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "funding_normalized_positive",
                    "funding_rate": funding,
                    "bars_in_trade": self.bars_in_trade
                })

            if self.bars_in_trade >= 18:  # 18 hours max hold
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "time_stop_exhaustion",
                    "bars_in_trade": self.bars_in_trade
                })

            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime guard: avoid active systemic meltdown
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.75:
            return None

        # Edge 1 & 2: Platform features
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        fng = ctx.features.get("fear_greed_index", 50)

        # Edge 3: Event boundary trigger on RSI
        # Instead of static overlap (RSI < 35 continuously), trigger on the bullish turn / hook
        rsi_curr = self._rsi(closes, period=14)
        rsi_prev = self._rsi(closes[:-1], period=14)
        if rsi_curr is None or rsi_prev is None:
            return None

        # Catalyst event: RSI was oversold (<= 35) and is now turning back upward
        rsi_hook_event = (rsi_prev <= 35.0) and (rsi_curr > rsi_prev) and (rsi_curr < 45.0)

        # Check macro/sentiment confluence
        fng_bearish = fng <= 35
        crowd_short = funding <= 0.0

        if rsi_hook_event and fng_bearish and crowd_short:
            # Scale confidence based on extremity of funding and fear
            base_conf = 0.65
            if funding < -0.00005:
                base_conf += 0.15
            if fng <= 25:
                base_conf += 0.10
            confidence = min(round(base_conf, 2), 0.95)

            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "triple_edge_oversold_inflection",
                    "rsi": round(rsi_curr, 2),
                    "rsi_prev": round(rsi_prev, 2),
                    "funding_rate": funding,
                    "fear_greed": fng,
                    "regime": regime
                }
            )

        return None