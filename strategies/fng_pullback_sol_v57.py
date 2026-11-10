from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        current_close = ctx.bar.close
        sma20 = self._sma(closes, 20)
        atr14 = self._atr(highs, lows, closes, 14)

        if sma20 is None or atr14 is None:
            return None

        # Position Management & Structural Dynamic Exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time stop to prevent capital tie-up
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "timed_exit_max_bars", "bars_held": bars_held})

            # Long Exit: Price mean-reverts back above SMA20 or sentiment normalizes towards positive
            if pos_dir == "long":
                if current_close >= sma20 or sentiment > -0.05:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_mean_reverted", "close": current_close, "sma20": sma20, "sentiment": sentiment})

            # Short Exit: Price mean-reverts back below SMA20 or sentiment cools down
            elif pos_dir == "short":
                if current_close <= sma20 or sentiment < 0.05:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_mean_reverted", "close": current_close, "sma20": sma20, "sentiment": sentiment})

            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid extreme breakdown crisis where dip-buying is catastrophic
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        ref_high = max(highs[-self.lookback:])
        ref_low = min(lows[-self.lookback:])

        # Dynamic stop loss based on recent volatility (ATR) rather than flat bps
        atr_bps = (atr14 / current_close) * 10000.0
        dyn_sl_bps = max(250.0, min(550.0, atr_bps * 1.8))
        dyn_tp_bps = max(350.0, min(700.0, atr_bps * 2.5))

        # Long Setup: Fearful sentiment + distinct 3% pullback from 48-bar high
        pullback_ratio = (ref_high - current_close) / ref_high
        if sentiment < -0.30 and pullback_ratio >= self.pullback_pct:
            # Prevent catching falling knives if already far below SMA without stabilization
            if current_close > ref_low:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(sentiment) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=dyn_sl_bps,
                    take_profit_bps=dyn_tp_bps,
                    metadata={
                        "reason": "sentiment_fear_dip_entry",
                        "sentiment": sentiment,
                        "pullback_ratio": pullback_ratio,
                        "ref_high": ref_high,
                        "sma20": sma20,
                        "atr_bps": atr_bps
                    }
                )

        # Short Setup: Euphoric sentiment + distinct 3% rally from 48-bar low
        rally_ratio = (current_close - ref_low) / ref_low
        if sentiment > 0.30 and rally_ratio >= self.pullback_pct:
            if current_close < ref_high:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + sentiment * 0.4)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=dyn_sl_bps,
                    take_profit_bps=dyn_tp_bps,
                    metadata={
                        "reason": "sentiment_greed_rally_fade",
                        "sentiment": sentiment,
                        "rally_ratio": rally_ratio,
                        "ref_low": ref_low,
                        "sma20": sma20,
                        "atr_bps": atr_bps
                    }
                )

        return None