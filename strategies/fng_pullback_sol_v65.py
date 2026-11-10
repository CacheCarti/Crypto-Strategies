from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSentimentGatedPullback(Strategy):
    METADATA = {
        "name": "SolSentimentGatedPullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 50,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.pullback_pct = 0.035        # 3.5% pullback / rally trigger for higher conviction
        self.cooldown_bars = 8          # mandatory cooldown to prevent overtrading and friction bleed
        self.last_exit_bar = -999
        self.mean_period = 24           # 24-hour mean for reversion target
        self.rsi_period = 14

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback:
            return None

        current_price = ctx.bar.close
        sentiment = ctx.features.get("twitter_sentiment", 0.0)
        
        # Calculate trailing mean and RSI for secondary momentum exhaustion confirmation
        sma_mean = sum(closes[-self.mean_period:]) / self.mean_period
        rsi = self._rsi(closes, self.rsi_period)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Long Exit: Price crossed above mean OR sentiment normalized/turned bullish (> 0.0)
            if pos_dir == "long":
                if current_price >= sma_mean or sentiment > 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "price": current_price,
                            "sma_mean": sma_mean,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        }
                    )
            # Short Exit: Price crossed below mean OR sentiment normalized/turned bearish (< 0.0)
            elif pos_dir == "short":
                if current_price <= sma_mean or sentiment < 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "price": current_price,
                            "sma_mean": sma_mean,
                            "sentiment": sentiment,
                            "rsi": rsi,
                        }
                    )
            return None

        # Respect cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter out extreme market crisis to avoid catching falling knives
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rolling_high = max(highs[-self.lookback:])
        rolling_low = min(lows[-self.lookback:])

        # Buy Dip Setup: Extreme fear + 3.5%+ pullback from high + RSI oversold (< 42)
        if sentiment <= -0.30:
            drawdown_from_high = (rolling_high - current_price) / rolling_high
            if drawdown_from_high >= self.pullback_pct and rsi < 42.0:
                # Scale confidence by sentiment extremity
                conf = min(0.9, 0.65 + abs(sentiment) * 0.25)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fearful_dip_pullback",
                        "sentiment": sentiment,
                        "drawdown_pct": round(drawdown_from_high * 100, 2),
                        "rolling_high": rolling_high,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    }
                )

        # Sell Rally Setup: Extreme euphoria + 3.5%+ rally from low + RSI overbought (> 58)
        if sentiment >= 0.30:
            rally_from_low = (current_price - rolling_low) / rolling_low
            if rally_from_low >= self.pullback_pct and rsi > 58.0:
                conf = min(0.9, 0.65 + abs(sentiment) * 0.25)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "euphoric_rally_exhaustion",
                        "sentiment": sentiment,
                        "rally_pct": round(rally_from_low * 100, 2),
                        "rolling_low": rolling_low,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                    }
                )

        return None