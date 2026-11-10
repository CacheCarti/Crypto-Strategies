from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03  # 3% pullback threshold
        self.cooldown_bars = 6    # mandatory post-exit cooldown
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 18   # time-based exit to prevent bleed

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Slow sentiment filter (proxy via Fear & Greed, backtestable)
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = ctx.bar.close

        # Indicators
        sma_24 = self._sma(closes, 24)
        atr_14 = self._atr(highs, lows, closes, 14)
        if sma_24 is None or atr_14 is None:
            return None

        # Structure/Volatility dynamic stop calculations
        vol_sl_bps = max(220.0, min(500.0, (atr_14 * 2.2 / current_close) * 10000.0))
        vol_tp_bps = vol_sl_bps * 1.5

        # Check existing position for exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            # 1. Timed exit to limit structural drag
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_max_hold_reached", "bars_held": bars_held, "sentiment": sentiment}
                )

            # 2. Mean reversion target reached or sentiment normalized
            if direction == "long":
                if current_close >= sma_24 or sentiment > -0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "long_mean_reversion_target", "close": current_close, "sma24": sma_24, "sentiment": sentiment}
                    )
            elif direction == "short":
                if current_close <= sma_24 or sentiment < 0.05:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={"reason": "short_mean_reversion_target", "close": current_close, "sma24": sma_24, "sentiment": sentiment}
                    )
            return None

        # Cooldown guard to eliminate overtrading and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Price triggers: 3% pullback from recent 48-bar swing extreme
        period_high = max(highs[-self.lookback:])
        period_low = min(lows[-self.lookback:])
        dip_from_high = (period_high - current_close) / period_high
        rally_from_low = (current_close - period_low) / period_low

        # 1. Long Entry: Fearful sentiment + Dip >= 3% from 48-bar high
        if sentiment <= -0.30 and dip_from_high >= self.pullback_pct:
            # Entry condition confirmation: price must be below sma_24 (oversold discount)
            if current_close < sma_24:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=vol_sl_bps,
                    take_profit_bps=vol_tp_bps,
                    metadata={
                        "reason": "sentiment_fear_dip_pullback",
                        "sentiment": sentiment,
                        "dip_from_high": round(dip_from_high, 4),
                        "period_high": period_high,
                        "sma_24": sma_24,
                        "atr_14": atr_14
                    }
                )

        # 2. Short Entry: Euphoric sentiment + Rally >= 3% from 48-bar low
        if sentiment >= 0.30 and rally_from_low >= self.pullback_pct:
            # Entry condition confirmation: price must be above sma_24 (overbought premium)
            if current_close > sma_24:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=vol_sl_bps,
                    take_profit_bps=vol_tp_bps,
                    metadata={
                        "reason": "sentiment_euphoria_rally_pullback",
                        "sentiment": sentiment,
                        "rally_from_low": round(rally_from_low, 4),
                        "period_low": period_low,
                        "sma_24": sma_24,
                        "atr_14": atr_14
                    }
                )

        return None