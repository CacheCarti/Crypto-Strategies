from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class EuropeDriftFollow(Strategy):
    METADATA = {
        "name": "Europe Drift Follow ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "twitter_sentiment"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_entry_bar = -self.entry_cooldown_bars
        self.median_range_period = 24 # For median range calculation
        self.session_length_bars = 5 # 7-12 UTC is 5 hours
        self.min_move_pct = 0.005 # 0.5% move for decisiveness
        self.max_exhaustion_pct = 0.02 # 2% move in session is considered exhausted

        # For median range calculation
        self.ranges = []

    def _median(self, data):
        if not data:
            return 0
        sorted_data = sorted(data)
        mid = len(sorted_data) // 2
        if len(sorted_data) % 2 == 0:
            return (sorted_data[mid - 1] + sorted_data[mid]) / 2
        return sorted_data[mid]

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update historical ranges for median range calculation
        if len(highs) > 1 and len(lows) > 1:
            self.ranges.append(highs[-1] - lows[-1])
            if len(self.ranges) > self.median_range_period:
                self.ranges.pop(0)

        median_daily_range = self._median(self.ranges)

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        twitter_sentiment = ctx.features.get("twitter_sentiment", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Cooldown check
        if ctx.bar_index < self.last_entry_bar + self.entry_cooldown_bars:
            return None

        # --- Entry Logic (European Morning 7-12 UTC) ---
        if 7 <= current_hour < (7 + self.session_length_bars):
            # Only consider entry if we haven't entered today
            if ctx.bar_index > self.last_entry_bar + (24 * 60 / 60): # At least 24 bars (hours) since last entry
                session_closes = ctx.closes(self.session_length_bars + 1) # Need current bar and previous 5 hours
                session_opens = ctx.opens(self.session_length_bars + 1)
                session_highs = ctx.highs(self.session_length_bars + 1)
                session_lows = ctx.lows(self.session_length_bars + 1)

                if len(session_closes) >= self.session_length_bars + 1:
                    session_open_price = session_opens[0]
                    current_price = ctx.bar.close

                    session_high = max(session_highs[:-1]) # Max high of the session so far
                    session_low = min(session_lows[:-1])   # Min low of the session so far

                    price_change = current_price - session_open_price
                    price_change_pct = price_change / session_open_price

                    # Determine if the session move is decisive
                    decisive_move_threshold = median_daily_range * 0.5 # require 50% of median range or 0.5%
                    is_decisive = abs(price_change_pct) > self.min_move_pct and abs(price_change) > decisive_move_threshold

                    # Check for exhaustion (price has moved too much in one direction)
                    is_exhausted = abs(price_change_pct) > self.max_exhaustion_pct

                    # Avoid single-bar spikes (ensure move is sustained over multiple bars)
                    # Check if the current bar's move is disproportionately large compared to the session so far
                    current_bar_move = abs(ctx.bar.close - ctx.bar.open)
                    avg_session_bar_move = (session_high - session_low) / (self.session_length_bars if self.session_length_bars > 0 else 1)
                    is_spike = current_bar_move > (avg_session_bar_move * 2) # Current bar move is double the average session bar move

                    if is_decisive and not is_exhausted and not is_spike:
                        # Regime gating
                        is_bullish_regime = (trend_regime == "bull" or fear_greed >= 60 or twitter_sentiment > 0.3)
                        is_bearish_regime = (trend_regime == "bear" or fear_greed <= 40 or twitter_sentiment < -0.3)
                        
                        # Avoid trading in highly volatile or crisis regimes
                        if regime in ["HIGH_VOL", "CRISIS", "MELTDOWN"]:
                            return None

                        if price_change_pct > 0: # Bullish drift
                            if is_bullish_regime or trend_regime == "neutral": # Only long if regime is not explicitly bearish
                                if not ctx.has_position():
                                    self.last_entry_bar = ctx.bar_index
                                    return ctx.signal("long", confidence=0.75, metadata={
                                        "reason": "europe_session_bullish_drift",
                                        "session_open": session_open_price,
                                        "current_price": current_price,
                                        "price_change_pct": price_change_pct,
                                        "median_daily_range": median_daily_range,
                                        "fear_greed": fear_greed,
                                        "twitter_sentiment": twitter_sentiment,
                                        "trend_regime": trend_regime
                                    })
                        elif price_change_pct < 0: # Bearish drift
                            if is_bearish_regime or trend_regime == "neutral": # Only short if regime is not explicitly bullish
                                if not ctx.has_position():
                                    self.last_entry_bar = ctx.bar_index
                                    return ctx.signal("short", confidence=0.75, metadata={
                                        "reason": "europe_session_bearish_drift",
                                        "session_open": session_open_price,
                                        "current_price": current_price,
                                        "price_change_pct": price_change_pct,
                                        "median_daily_range": median_daily_range,
                                        "fear_greed": fear_greed,
                                        "twitter_sentiment": twitter_sentiment,
                                        "trend_regime": trend_regime
                                    })

        # --- Exit Logic (End of session or structure stop) ---
        if ctx.has_position():
            # Exit by UTC 12 (end of European morning session)
            if current_hour >= (7 + self.session_length_bars) and current_hour < 13: # Exit between 12 and 1 PM UTC
                 # Only exit if position was opened in the current "day" (since last_entry_bar)
                if ctx.bar_index - ctx.position["bars_held"] >= self.last_entry_bar: # Check if current position was the one opened today
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "session_end_time",
                        "current_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close
                    })

            # Structure stop: if price moves against us significantly after entry
            # Simple structure stop: 2-bar reversal against the trend
            if ctx.position_direction() == "long":
                if ctx.bar.close < closes[-2] and closes[-2] < closes[-3]: # Two consecutive lower closes
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "structure_stop_long",
                        "current_price": ctx.bar.close,
                        "closes_2_ago": closes[-2],
                        "closes_3_ago": closes[-3]
                    })
            elif ctx.position_direction() == "short":
                if ctx.bar.close > closes[-2] and closes[-2] > closes[-3]: # Two consecutive higher closes
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "structure_stop_short",
                        "current_price": ctx.bar.close,
                        "closes_2_ago": closes[-2],
                        "closes_3_ago": closes[-3]
                    })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy yet, just logging for now.
        pass