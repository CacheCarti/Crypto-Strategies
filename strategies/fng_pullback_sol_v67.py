from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentMeanReversion(Strategy):
    METADATA = {
        "name": "SolSentimentMeanReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 24

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
        sma_val = self._sma(closes, self.lookback)
        atr_val = self._atr(highs, lows, closes, 14)

        if sma_val is None or atr_val is None or current_close <= 0:
            return None

        bars_since_exit = ctx.bar_index - self.last_exit_bar
        has_pos = ctx.has_position()

        if has_pos:
            pos_dir = ctx.position_direction()
            bars_in_trade = ctx.bar_index - self.entry_bar

            # Volatility-adjusted dynamic target / timed exit
            if pos_dir == "long":
                if current_close >= sma_val or sentiment > 0.1 or bars_in_trade >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_timed_exit",
                            "close": current_close,
                            "sma": sma_val,
                            "sentiment": sentiment,
                            "bars_held": bars_in_trade,
                        },
                    )
            elif pos_dir == "short":
                if current_close <= sma_val or sentiment < -0.1 or bars_in_trade >= self.max_hold_bars:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_timed_exit",
                            "close": current_close,
                            "sma": sma_val,
                            "sentiment": sentiment,
                            "bars_held": bars_in_trade,
                        },
                    )
            return None

        if bars_since_exit < self.cooldown_bars:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])

        pullback_from_high = (recent_high - current_close) / recent_high if recent_high > 0 else 0.0
        bounce_from_low = (current_close - recent_low) / recent_low if recent_low > 0 else 0.0

        # Structural stops based on ATR
        atr_bps = (atr_val / current_close) * 10000.0
        stop_bps = max(250.0, min(550.0, atr_bps * 2.5))
        tp_bps = max(350.0, min(750.0, atr_bps * 3.5))

        # Long: sentiment is fearful (<= -0.3) & price has pulled back >= 3% from recent high
        if sentiment <= -0.3 and pullback_from_high >= self.pullback_pct:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, max(0.5, 0.5 + abs(sentiment) * 0.4))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=36000,
                metadata={
                    "reason": "fearful_dip_pullback",
                    "sentiment": sentiment,
                    "pullback_pct": pullback_from_high,
                    "high": recent_high,
                    "atr": atr_val,
                },
            )

        # Short: sentiment is euphoric (>= 0.3) & price extended >= 3% from recent low
        if sentiment >= 0.3 and bounce_from_low >= self.pullback_pct:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, max(0.5, 0.5 + abs(sentiment) * 0.4))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=36000,
                metadata={
                    "reason": "euphoric_rally_exhaustion",
                    "sentiment": sentiment,
                    "bounce_pct": bounce_from_low,
                    "low": recent_low,
                    "atr": atr_val,
                },
            )

        return None