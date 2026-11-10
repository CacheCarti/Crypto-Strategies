from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeeklyCloseBTC(Strategy):
    METADATA = {
        "name": "Weekly Close BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 48 * 3600, # Hold for roughly 48 hours (Friday evening to Sunday evening)
        "warmup_bars": 50, # Need enough bars for 26-period EMA and volume
        "required_features": ["fear_greed_index", "twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.volume_period = 20
        self.last_trade_bar = -self.warmup_bars # Initialize to allow trading after warmup

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars or len(volumes) < self.warmup_bars:
            return None

        # Cooldown logic: wait at least 12 bars (12 hours) after a trade
        if ctx.bar_index - self.last_trade_bar < 12:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_weekday = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        sma_volume = self._sma(volumes, self.volume_period)

        if ema is None or sma_volume is None:
            return None

        current_close = ctx.bar.close
        current_volume = ctx.bar.volume

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        twitter_sentiment = ctx.features.get("twitter_sentiment", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # --- Entry Logic: Friday evening close ---
        # Look for entry on Friday (weekday 4), between 18:00 and 23:00 UTC (last 6 hours before Saturday 00:00 UTC)
        if current_weekday == 4 and 18 <= current_hour <= 23:
            # Long condition: Price closes significantly above EMA, high volume, positive sentiment/trend
            if current_close > ema * 1.002 and current_volume > sma_volume * 1.2: # 0.2% above EMA, 20% above avg volume
                if fear_greed > 45 and twitter_sentiment > 0.1 and trend_regime == "bull":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "friday_close_long_bullish_confirmation",
                            "close": current_close,
                            "ema": ema,
                            "volume": current_volume,
                            "avg_volume": sma_volume,
                            "fear_greed": fear_greed,
                            "twitter_sentiment": twitter_sentiment,
                            "trend_regime": trend_regime
                        }
                    )
            # Short condition: Price closes significantly below EMA, high volume, negative sentiment/trend
            elif current_close < ema * 0.998 and current_volume > sma_volume * 1.2: # 0.2% below EMA, 20% above avg volume
                if fear_greed < 55 and twitter_sentiment < -0.1 and trend_regime == "bear":
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "friday_close_short_bearish_confirmation",
                            "close": current_close,
                            "ema": ema,
                            "volume": current_volume,
                            "avg_volume": sma_volume,
                            "fear_greed": fear_greed,
                            "twitter_sentiment": twitter_sentiment,
                            "trend_regime": trend_regime
                        }
                    )

        # --- Exit Logic: Sunday evening ---
        # If we have a position, attempt to flatten it on Sunday (weekday 6), between 18:00 and 23:00 UTC
        if ctx.has_position() and current_weekday == 6 and 18 <= current_hour <= 23:
            # Only exit if the position was opened for this weekly close strategy.
            # We assume any open position within this timeframe is from our Friday entry.
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "sunday_evening_exit",
                    "current_price": current_close,
                    "bar_timestamp": ctx.bar.timestamp.isoformat()
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        # print(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held for {position['bars_held']} bars.")
        pass