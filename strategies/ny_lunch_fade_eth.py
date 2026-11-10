from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class IntradayLiquidityPause(Strategy):
    METADATA = {
        "name": "Intraday Liquidity Pause",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-3 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_bar_index = -1
        self.cooldown_bars = 12 # 12 hours cooldown after a trade
        self.max_hold_bars = 4 # Exit by UTC 21 (4 hours after 17 UTC entry window)
        self.morning_move_start_price = None
        self.morning_move_peak_price = None
        self.morning_move_direction = None

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
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.entry_bar_index < self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Regime filter: Skip crisis regimes
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Trend filter: Skip strongly trending regimes, especially against fade
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

        # Logic for exiting an existing position
        if ctx.has_position():
            pos = ctx.position
            bars_held = ctx.bar_index - self.entry_bar_index
            
            # Exit by UTC 21 (current_hour >= 21)
            # Or if bars_held exceeds max_hold_bars
            if current_hour >= 21 or bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "time_based_exit",
                    "entry_price": pos["entry_price"],
                    "current_price": current_close,
                    "bars_held": bars_held,
                    "exit_hour": current_hour
                })

            # Exit if half the morning move retraces
            if self.morning_move_start_price is not None and self.morning_move_peak_price is not None:
                move_magnitude = abs(self.morning_move_peak_price - self.morning_move_start_price)
                if move_magnitude > 0:
                    if self.morning_move_direction == "long" and pos["direction"] == "short":
                        # Short position fading a long move. Exit if price recovers 50% of the dip from peak.
                        # Or if price moves back to 50% of the morning move range
                        target_price = self.morning_move_peak_price - (move_magnitude / 2.0)
                        if current_close >= target_price:
                             return ctx.signal("flat", confidence=0.8, metadata={
                                "reason": "half_morning_move_retracement_exit",
                                "entry_price": pos["entry_price"],
                                "current_price": current_close,
                                "target_price": target_price,
                                "move_magnitude": move_magnitude
                            })
                    elif self.morning_move_direction == "short" and pos["direction"] == "long":
                        # Long position fading a short move. Exit if price recovers 50% of the rally from trough.
                        # Or if price moves back to 50% of the morning move range
                        target_price = self.morning_move_peak_price + (move_magnitude / 2.0)
                        if current_close <= target_price:
                            return ctx.signal("flat", confidence=0.8, metadata={
                                "reason": "half_morning_move_retracement_exit",
                                "entry_price": pos["entry_price"],
                                "current_price": current_close,
                                "target_price": target_price,
                                "move_magnitude": move_magnitude
                            })
            return None # Hold position if no exit condition met

        # Reset morning move tracking outside of trading hours
        if current_hour == 12: # Reset at 12 UTC, before 13 UTC start
            self.morning_move_start_price = current_close
            self.morning_move_peak_price = current_close
            self.morning_move_direction = None
        elif self.morning_move_start_price is not None and 13 <= current_hour <= 16:
            # Track morning move (UTC 13-16)
            if self.morning_move_direction is None:
                if current_close > self.morning_move_peak_price:
                    self.morning_move_peak_price = current_close
                    self.morning_move_direction = "long"
                elif current_close < self.morning_move_peak_price:
                    self.morning_move_peak_price = current_close
                    self.morning_move_direction = "short"
            else:
                if self.morning_move_direction == "long" and current_close > self.morning_move_peak_price:
                    self.morning_move_peak_price = current_close
                elif self.morning_move_direction == "short" and current_close < self.morning_move_peak_price:
                    self.morning_move_peak_price = current_close

        # Entry logic: UTC 16-19 liquidity lull
        if 16 <= current_hour <= 19 and self.morning_move_start_price is not None and self.morning_move_peak_price is not None:
            move_magnitude = abs(self.morning_move_peak_price - self.morning_move_start_price)
            
            # Require a significant one-sided move (> 0.5% of price, roughly 50bps)
            # Use average true range (ATR) for volatility context
            highs = ctx.highs(14)
            lows = ctx.lows(14)
            prev_closes = ctx.closes(15)
            if len(highs) < 14 or len(lows) < 14 or len(prev_closes) < 15: return None
            
            trs = []
            for i in range(1, len(prev_closes)):
                tr = max(highs[i]-lows[i], abs(highs[i]-prev_closes[i-1]), abs(lows[i]-prev_closes[i-1]))
                trs.append(tr)
            atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0.0

            if atr == 0: return None # Avoid division by zero

            # Morning move should be at least 1.5x ATR to be considered "unusually one-sided"
            if move_magnitude / current_close < 0.005 or move_magnitude < 1.5 * atr: # 0.5% or 1.5 ATR
                return None

            # Check for close-confirmed entry (price retracing slightly from peak)
            # Use Z-score for recent price action confirmation
            price_zscore = self._zscore(closes, 20) # Z-score of last 20 closes
            if price_zscore is None: return None

            signal_direction = None
            if self.morning_move_direction == "long":
                # Fade long move -> look for short
                # Price should be below peak and current momentum should be weakening/negative (negative z-score)
                if current_close < self.morning_move_peak_price and price_zscore < -0.5: # -0.5 std dev below mean
                    signal_direction = "short"
            elif self.morning_move_direction == "short":
                # Fade short move -> look for long
                # Price should be above trough and current momentum should be weakening/positive (positive z-score)
                if current_close > self.morning_move_peak_price and price_zscore > 0.5: # +0.5 std dev above mean
                    signal_direction = "long"

            if signal_direction:
                # Add a sentiment filter: don't fade against extreme sentiment
                fear_greed = ctx.features.get("fear_greed_index", 50)
                if signal_direction == "long" and fear_greed < 25: # Don't buy into extreme fear
                    return None
                if signal_direction == "short" and fear_greed > 75: # Don't short into extreme greed
                    return None
                
                # Further filter: avoid fading if market trend is strongly aligned with morning move
                if (signal_direction == "short" and trend_regime == "bull" and trend_confidence > 0.6) or \
                   (signal_direction == "long" and trend_regime == "bear" and trend_confidence > 0.6):
                    return None

                self.entry_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=0.6 + (0.4 * min(1, move_magnitude / (2.5 * atr))), # Confidence scales with move magnitude relative to ATR
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": f"fade_intraday_liquidity_pause_{signal_direction}",
                        "morning_move_start_price": self.morning_move_start_price,
                        "morning_move_peak_price": self.morning_move_peak_price,
                        "morning_move_direction": self.morning_move_direction,
                        "move_magnitude": move_magnitude,
                        "current_close": current_close,
                        "current_hour": current_hour,
                        "price_zscore": price_zscore,
                        "atr": atr,
                        "fear_greed_index": fear_greed
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset entry bar index to allow cooldown mechanism to work
        pass