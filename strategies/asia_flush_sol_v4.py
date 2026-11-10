from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolAsiaFlushRecovery(Strategy):
    METADATA = {
        "name": "SOL Asia Flush Recovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600, # Max hold until UTC 21 (9 hours * 3600 seconds)
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"], # Using these as general market sentiment proxies
    }

    def initialize(self, ctx: BarContext) -> None:
        self.flush_period_start_hour = 0  # UTC 0
        self.flush_period_end_hour = 8   # UTC 8
        self.stabilization_start_hour = 8 # UTC 8
        self.stabilization_end_hour = 12  # UTC 12
        self.exit_hour = 21              # UTC 21
        self.flush_lookback_bars = 8     # 8 hours for flush detection
        self.stabilization_lookback_bars = 4 # 4 hours for stabilization detection
        self.min_flush_pct = 1.5         # Minimum 1.5% drop
        self.max_recovery_pct = 0.5      # Max recovery during stabilization
        self.min_volume_increase = 1.5   # Volume during flush should be higher than avg
        self.cooldown_bars = 6           # Cooldown after a trade (6 hours)
        self.last_trade_bar = -self.cooldown_bars

        # Indicators for regime filtering
        self.long_ema_period = 30
        self.short_ema_period = 10
        self.stddev_period = 20

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for existing positions
        if ctx.has_position():
            position_direction = ctx.position_direction()
            position_entry_price = ctx.position["entry_price"]
            
            # Exit by UTC 21 if still in position
            if current_hour >= self.exit_hour and position_direction == "long":
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit_utc21",
                    "entry_price": position_entry_price,
                    "current_price": ctx.bar.close,
                    "exit_hour": self.exit_hour
                })
            
            # Partial retracement exit: if price drops significantly from peak profit
            # This is handled by the platform's trailing stop, but we can add a custom one if needed.
            # For simplicity, relying on platform trailing stop with declared_tp_bps.
            return None # Hold position if no exit condition met

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filters
        long_ema = self._ema(closes, self.long_ema_period)
        short_ema = self._ema(closes, self.short_ema_period)
        std_dev = self._stddev(closes, self.stddev_period)
        
        if long_ema is None or short_ema is None or std_dev is None:
            return None

        # Avoid trading in highly volatile or strongly trending environments against the recovery
        # Only trade if market is not in crisis and not extremely volatile
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Avoid trading if there's a strong downtrend (short EMA below long EMA)
        if short_ema < long_ema * 0.99: # 1% buffer for trend confirmation
            return None

        # Avoid trading if the recent volatility is too high for a recovery play
        # (e.g., if std_dev is very large, market is too erratic for a predictable recovery)
        # Using a proxy for "too high" based on current price
        avg_price = (ctx.bar.high + ctx.bar.low + ctx.bar.close) / 3
        # If 1-period std dev is more than 0.5% of price, consider it too volatile for stable recovery
        if std_dev / avg_price * 100 > 0.5: 
             return None

        # Detect flush during Asia session (UTC 0-8)
        is_flush_period = current_hour >= self.flush_period_start_hour and current_hour < self.flush_period_end_hour
        
        # Detect stabilization period (UTC 8-12)
        is_stabilization_period = current_hour >= self.stabilization_start_hour and current_hour < self.stabilization_end_hour

        if is_stabilization_period:
            # Look back to see if there was a flush during the flush period
            flush_closes = closes[-self.flush_lookback_bars-self.stabilization_lookback_bars : -self.stabilization_lookback_bars]
            flush_lows = lows[-self.flush_lookback_bars-self.stabilization_lookback_bars : -self.stabilization_lookback_bars]
            flush_volumes = volumes[-self.flush_lookback_bars-self.stabilization_lookback_bars : -self.stabilization_lookback_bars]

            if len(flush_closes) < self.flush_lookback_bars:
                return None

            max_price_before_flush = max(flush_closes)
            min_price_during_flush = min(flush_lows)
            
            flush_drop_pct = (max_price_before_flush - min_price_during_flush) / max_price_before_flush * 100

            # Check for significant drop (flush)
            if flush_drop_pct >= self.min_flush_pct:
                # Check for stabilization: price should not have recovered too much
                # and current bar should be holding above the flush low
                
                # Current price relative to the low of the flush
                current_recovery_from_low_pct = (ctx.bar.close - min_price_during_flush) / min_price_during_flush * 100
                
                # Check for higher volume during the flush compared to average
                avg_volume_before_flush = self._sma(volumes[:-self.flush_lookback_bars], self.flush_lookback_bars)
                flush_avg_volume = sum(flush_volumes) / len(flush_volumes) if flush_volumes else 0

                if (current_recovery_from_low_pct <= self.max_recovery_pct and 
                    ctx.bar.close > min_price_during_flush and
                    flush_avg_volume > avg_volume_before_flush * self.min_volume_increase if avg_volume_before_flush else True):
                    
                    # Additional check: current bar's close should be above its open (green candle) or relatively stable
                    # And it should be above the low of the flush period
                    if ctx.bar.close > ctx.bar.open and ctx.bar.close > min_price_during_flush:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.8, metadata={
                            "reason": "sol_asia_flush_recovery",
                            "flush_drop_pct": flush_drop_pct,
                            "min_price_during_flush": min_price_during_flush,
                            "current_recovery_pct": current_recovery_from_low_pct,
                            "flush_avg_volume": flush_avg_volume,
                            "avg_volume_before_flush": avg_volume_before_flush,
                            "current_close": ctx.bar.close,
                            "current_hour": current_hour,
                            "regime": ctx.market.get("regime", "NORMAL"),
                            "trend_regime": ctx.market.get("trend_regime", "neutral"),
                            "short_ema": short_ema,
                            "long_ema": long_ema,
                            "eth_return_pct": ctx.features.get("eth_return_pct", 0.0),
                            "btc_return_pct": ctx.features.get("btc_return_pct", 0.0),
                        })
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally, adapt parameters or log results
        pass