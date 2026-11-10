from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversion(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14):
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
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        btc_24h = ctx.features.get("btc_return_pct", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit", "regime": market_regime})
            return None

        sma24 = self._sma(closes, 24)
        if sma24 is None:
            return None

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= sma24 or sentiment > 0.1:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_mean_reversion_or_sentiment_norm", "close": current_close, "sma24": sma24, "sentiment": sentiment})
            elif pos_dir == "short":
                if current_close <= sma24 or sentiment < -0.1:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_mean_reversion_or_sentiment_norm", "close": current_close, "sma24": sma24, "sentiment": sentiment})
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        max_high = max(recent_highs)
        min_low = min(recent_lows)

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        atr_pct = atr / current_close
        if atr_pct < 0.008 or atr_pct > 0.06:
            return None

        pullback_from_high = (max_high - current_close) / max_high
        rally_from_low = (current_close - min_low) / min_low

        if sentiment <= -0.25 and pullback_from_high >= self.pullback_pct and btc_24h > -4.0:
            prev_close = closes[-2]
            if current_close >= prev_close:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_fear_dip_bounce",
                        "sentiment": sentiment,
                        "pullback_pct": pullback_from_high,
                        "max_high": max_high,
                        "btc_24h": btc_24h,
                        "close": current_close,
                    },
                )

        if sentiment >= 0.25 and rally_from_low >= self.pullback_pct and btc_24h < 4.0:
            prev_close = closes[-2]
            if current_close <= prev_close:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sentiment_euphoria_rally_fade",
                        "sentiment": sentiment,
                        "rally_pct": rally_from_low,
                        "min_low": min_low,
                        "btc_24h": btc_24h,
                        "close": current_close,
                    },
                )

        return None