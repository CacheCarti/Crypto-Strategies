from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityDriftRecovery(Strategy):
    METADATA = {
        "name": "Volatility Drift Recovery BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours
        "warmup_bars": 60,
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period = 10
        self.recovery_period = 5
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

        # ATR for volatility assessment
        self.atr_period = 14
        # Z-score for price deviation
        self.zscore_period = 20
        # SMA for stabilization check
        self.sma_period = 10

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = self._sma(values, period)
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        z_score_close = self._zscore(closes, self.zscore_period)
        sma_recovery = self._sma(closes, self.recovery_period)

        if current_atr is None or z_score_close is None or sma_recovery is None:
            return None

        # Regime filter: Avoid trading during extreme crisis
        if ctx.market.get("regime", "NORMAL") == "CRISIS":
            return None

        # Wildcard: Look for sharp negative price movement (flush)
        # We define a "flush" as a significant negative z-score AND a high current ATR relative to its recent average.
        # This implies a sharp drop with increased volatility.
        # We need a recent history of ATR to compare against.
        recent_atrs = [self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period, len(closes))]
        recent_atrs = [atr for atr in recent_atrs if atr is not None]

        if len(recent_atrs) < self.flush_period:
            return None

        avg_recent_atr = sum(recent_atrs[-self.flush_period:]) / self.flush_period
        
        is_flush_event = (
            z_score_close < -1.5 and # Price is significantly below its recent mean
            current_atr > avg_recent_atr * 1.5 # Current volatility is significantly higher than recent average
        )

        # Stabilization check: after a flush, price needs to stabilize
        # We look for the price to be near or above a short-term moving average, indicating recovery drift
        is_stabilized_recovery = (
            current_price > sma_recovery * 0.999 and # Price is at least 0.1% above the short-term SMA
            current_price < sma_recovery * 1.005 # And not too far above, indicating a gentle drift, not a pump
        )

        # Sentiment filter: only long if Fear & Greed is not "Extreme Greed"
        fear_greed_class = ctx.features.get("fear_greed_class", "Neutral")
        if fear_greed_class == "Extreme Greed":
            return None

        # Entry condition: A flush event has happened, and now we are seeing stabilization and recovery drift.
        # We also need to check that the flush event happened RECENTLY, not currently.
        # Let's check for the flush in the previous bar or two, and stabilization now.
        
        # Check for flush in the previous bar
        prev_closes = ctx.closes(self.warmup_bars + 1)
        prev_highs = ctx.highs(self.warmup_bars + 1)
        prev_lows = ctx.lows(self.warmup_bars + 1)

        prev_z_score_close = self._zscore(prev_closes[:-1], self.zscore_period)
        prev_atr = self._atr(prev_highs[:-1], prev_lows[:-1], prev_closes[:-1], self.atr_period)

        if prev_z_score_close is None or prev_atr is None:
            return None
        
        # Re-calculate avg_recent_atr for the previous bar context
        prev_recent_atrs = [self._atr(prev_highs[:i+1], prev_lows[:i+1], prev_closes[:i+1], self.atr_period) for i in range(self.atr_period, len(prev_closes) - 1)]
        prev_recent_atrs = [atr for atr in prev_recent_atrs if atr is not None]
        
        if len(prev_recent_atrs) < self.flush_period:
             return None
        
        prev_avg_recent_atr = sum(prev_recent_atrs[-self.flush_period:]) / self.flush_period

        prev_is_flush_event = (
            prev_z_score_close < -1.5 and
            prev_atr > prev_avg_recent_atr * 1.5
        )
        
        if not ctx.has_position():
            if prev_is_flush_event and is_stabilized_recovery:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "volatility_drift_recovery_long",
                        "prev_z_score": prev_z_score_close,
                        "current_z_score": z_score_close,
                        "prev_atr_ratio": prev_atr / prev_avg_recent_atr,
                        "current_atr": current_atr,
                        "sma_recovery": sma_recovery,
                        "current_price": current_price,
                        "fear_greed_class": fear_greed_class
                    }
                )
        elif ctx.position_direction() == "long":
            # If in a long position, check for exit conditions (e.g., if price drops below SMA again)
            if current_price < sma_recovery * 0.995: # If price drops 0.5% below SMA
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_exit_sma_breakdown",
                        "current_price": current_price,
                        "sma_recovery": sma_recovery,
                        "z_score": z_score_close,
                        "atr": current_atr
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown after any position closure
        self.last_trade_bar = ctx.bar_index
        pass