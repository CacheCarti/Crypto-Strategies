from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "FundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.funding_history = []

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)

        if len(closes) < self.lookback + 5:
            return None

        # Track recent funding rates to assess positioning crowding
        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.lookback:
            self.funding_history.pop(0)

        # In-position management
        if ctx.has_position():
            return None

        # Cooldown guard after trade exit to prevent churning
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid extreme meltdown crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime == "MELTDOWN" or crisis_score > 0.75:
            return None

        # Price momentum over divergence window
        price_change_pct = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        
        # Funding persistence: average over the lookback
        avg_funding = sum(self.funding_history) / len(self.funding_history)

        # Volatility state confirmation: price move must exceed normal noise
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None
        atr_pct = atr / closes[-1]

        # Breakout confirmation against recent local extremes
        recent_high = max(highs[-self.lookback:-1])
        recent_low = min(lows[-self.lookback:-1])

        # Short squeeze: Price pushing new highs while aggregate funding remains negative (shorts paying longs)
        # Material confirmation: breakout above recent high with price move > 1.2 * ATR%
        bullish_squeeze = (
            price_change_pct > (1.2 * atr_pct)
            and closes[-1] > recent_high
            and avg_funding < -0.00005
            and current_funding < 0.00002
        )

        # Long squeeze: Price breaking lows while funding remains positive (overcrowded longs trapped)
        # Material confirmation: breakdown below recent low with price drop > 1.2 * ATR%
        bearish_squeeze = (
            price_change_pct < (-1.2 * atr_pct)
            and closes[-1] < recent_low
            and avg_funding > 0.00005
            and current_funding > -0.00002
        )

        if bullish_squeeze:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_funding_divergence",
                    "price_change_pct": round(price_change_pct, 4),
                    "avg_funding": round(avg_funding, 6),
                    "current_funding": round(current_funding, 6),
                    "atr_pct": round(atr_pct, 4),
                    "price": closes[-1],
                },
            )

        if bearish_squeeze:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_squeeze_funding_divergence",
                    "price_change_pct": round(price_change_pct, 4),
                    "avg_funding": round(avg_funding, 6),
                    "current_funding": round(current_funding, 6),
                    "atr_pct": round(atr_pct, 4),
                    "price": closes[-1],
                },
            )

        return None