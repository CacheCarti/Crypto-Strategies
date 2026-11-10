from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentDipReversion(Strategy):
    METADATA = {
        "name": "SolSentimentDipReversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.03
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.entry_bar = -999

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.lookback)
        lows = ctx.lows(self.lookback)

        if len(closes) < self.METADATA["warmup_bars"] or len(highs) < self.lookback or len(lows) < self.lookback:
            return None

        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        current_close = ctx.bar.close
        sma_24 = self._sma(closes, 24)
        rsi_14 = self._rsi(closes, 14)

        if sma_24 is None or rsi_14 is None:
            return None

        # Position Management & Mean Reversion Exit Logic
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            if direction == "long":
                if current_close >= sma_24 or sentiment > -0.05 or rsi_14 > 65.0 or bars_held > 30:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "long_mean_reversion_or_sentiment_norm",
                        "close": current_close, "sma24": sma_24, "sentiment": sentiment, "bars_held": bars_held
                    })
            elif direction == "short":
                if current_close <= sma_24 or sentiment < 0.05 or rsi_14 < 35.0 or bars_held > 30:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "short_mean_reversion_or_sentiment_norm",
                        "close": current_close, "sma24": sma_24, "sentiment": sentiment, "bars_held": bars_held
                    })
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # High/Low reference levels over 48 bars (excluding current bar to avoid lookahead bias)
        recent_high = max(highs[:-1])
        recent_low = min(lows[:-1])

        # Regime and Volatility sanity checks: avoid extreme meltdown/crisis halts
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Long Setup:
        # 1) Sentiment is fearful (< -0.25)
        # 2) SOL pulled back >= 3% from 48-bar high
        # 3) Cross-asset stabilization confirmation (BTC 24h return >= -3.5%)
        # 4) Local exhaustion confirmation (RSI < 42)
        if sentiment < -0.25:
            pullback_from_high = (recent_high - current_close) / recent_high
            if pullback_from_high >= self.pullback_pct and btc_ret >= -3.5 and rsi_14 < 42.0:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fear_dip_cross_asset_stabilized",
                        "pullback_pct": pullback_from_high,
                        "sentiment": sentiment,
                        "btc_return_pct": btc_ret,
                        "rsi": rsi_14,
                        "high48": recent_high
                    }
                )

        # Short Setup:
        # 1) Sentiment is euphoric (> 0.25)
        # 2) SOL extended >= 3% from 48-bar low
        # 3) Cross-asset confirmation (BTC not excessively surging, btc_return <= 3.5%)
        # 4) Local extension confirmation (RSI > 58)
        if sentiment > 0.25:
            extension_from_low = (current_close - recent_low) / recent_low
            if extension_from_low >= self.pullback_pct and btc_ret <= 3.5 and rsi_14 > 58.0:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.6 + abs(sentiment) * 0.3)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "greed_rally_cross_asset_checked",
                        "extension_pct": extension_from_low,
                        "sentiment": sentiment,
                        "btc_return_pct": btc_ret,
                        "rsi": rsi_14,
                        "low48": recent_low
                    }
                )

        return None