from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapFade(Strategy):
    METADATA = {
        "name": "Session Gap Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for 4-hour holds
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars
        self.gap_threshold_pct = 0.005  # 0.5% gap
        self.gap_continuation_threshold_pct = 0.002 # 0.2% continuation
        self.max_gap_size_pct = 0.03 # Max 3% gap to fade
        self.sma_period = 20 # For trend filter
        self.rsi_period = 14 # For oversold/overbought filter
        self.volatility_period = 20 # For ATR
        self.min_atr_multiple = 1.5 # Min gap size relative to ATR

        self.us_close_price = None
        self.us_close_bar_index = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-min(period, len(trs)):]) / min(period, len(trs)) # sum last `period` or all if less

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day_of_week = ctx.bar.timestamp.weekday() # Monday is 0, Sunday is 6

        # Define session hours (UTC)
        # US Session: 13-22 UTC (roughly NYSE hours)
        # Asia Session: 0-9 UTC (roughly Tokyo/Shanghai hours)

        # Store US close price at 22:00 UTC
        if current_hour == 22 and self.us_close_price is None:
            self.us_close_price = ctx.bar.close
            self.us_close_bar_index = ctx.bar_index

        # Reset US close price at 23:00 UTC to prevent carrying it too long
        if current_hour == 23 and self.us_close_price is not None:
            self.us_close_price = None
            self.us_close_bar_index = None

        # Cooldown check
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Regime filters
        sma = self._sma(closes, self.sma_period)
        if sma is None:
            return None
        
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        atr = self._atr(highs, lows, closes, self.volatility_period)
        if atr is None or atr == 0:
            return None

        # Market context filters
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Only consider trading if not in a strong trend
        is_neutral_trend = trend_regime == "neutral" or ctx.market.get("trend_regime_confidence", 0.0) < 0.3
        
        # Avoid trading during extreme fear/greed
        is_favorable_sentiment = 30 <= fear_greed_index <= 70

        # Avoid trading against strong funding rate bias (fade funding rate)
        # If funding rate is very positive (longs paying shorts), implies bullish sentiment. Fading means short.
        # If funding rate is very negative (shorts paying longs), implies bearish sentiment. Fading means long.
        is_favorable_funding = abs(funding_rate) < 0.0002 # Neutral funding
        
        # Entry logic
        if not ctx.has_position() and self.us_close_price is not None:
            gap_size = ctx.bar.open - self.us_close_price
            gap_pct = gap_size / self.us_close_price

            # Asia session open (0-9 UTC) after US close (22 UTC)
            is_asia_open = 0 <= current_hour <= 9
            
            if is_asia_open and self.us_close_bar_index is not None and ctx.bar_index == self.us_close_bar_index + 1:
                # This is the first bar after the US close
                # We need to wait for a few bars to see if the gap continues or starts to fade
                # The actual check for gap fade will be on subsequent bars in Asia session
                pass # Do nothing on the immediate bar after US close

            # Check for gap fade during Asia session (after the first bar)
            if is_asia_open and self.us_close_price is not None and ctx.bar_index > self.us_close_bar_index + 1:
                # Calculate gap from US close to current bar's open
                gap_to_current_open_pct = (ctx.bar.open - self.us_close_price) / self.us_close_price
                
                # Calculate continuation from current bar's open to current bar's close
                continuation_pct = (ctx.bar.close - ctx.bar.open) / ctx.bar.open

                # Check for an unusually large gap (but not too large)
                is_large_gap = abs(gap_to_current_open_pct) > self.gap_threshold_pct
                is_not_too_large_gap = abs(gap_to_current_open_pct) < self.max_gap_size_pct
                is_significant_volatility = abs(gap_to_current_open_pct) > self.min_atr_multiple * (atr / ctx.bar.close)

                # Check for lack of continuation against the gap direction
                is_fading_long = gap_to_current_open_pct < 0 and continuation_pct > -self.gap_continuation_threshold_pct # Negative gap, but price is not continuing down
                is_fading_short = gap_to_current_open_pct > 0 and continuation_pct < self.gap_continuation_threshold_pct # Positive gap, but price is not continuing up

                # Check for overbought/oversold RSI in conjunction with gap
                is_oversold_rsi = rsi < 30
                is_overbought_rsi = rsi > 70

                # Determine confidence
                confidence = 0.0

                if is_large_gap and is_not_too_large_gap and is_significant_volatility and is_neutral_trend and is_favorable_sentiment:
                    if is_fading_long and is_oversold_rsi:
                        # Fade negative gap (go long)
                        confidence = 0.6 + ctx.rng.uniform(0, 0.2) # Base confidence + some randomness
                        if funding_rate < -0.0001: # Funding rate is negative, shorts paying longs, adds to long conviction
                            confidence += 0.1
                        
                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=min(1.0, confidence),
                            metadata={
                                "reason": "negative_gap_fade_oversold_rsi_neutral_trend",
                                "us_close": self.us_close_price,
                                "current_open": ctx.bar.open,
                                "gap_pct": gap_to_current_open_pct,
                                "continuation_pct": continuation_pct,
                                "rsi": rsi,
                                "sma": sma,
                                "atr": atr,
                                "trend_regime": trend_regime,
                                "fear_greed_index": fear_greed_index,
                                "funding_rate": funding_rate,
                            },
                        )
                    elif is_fading_short and is_overbought_rsi:
                        # Fade positive gap (go short)
                        confidence = 0.6 + ctx.rng.uniform(0, 0.2)
                        if funding_rate > 0.0001: # Funding rate is positive, longs paying shorts, adds to short conviction
                            confidence += 0.1

                        self.last_signal_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=min(1.0, confidence),
                            metadata={
                                "reason": "positive_gap_fade_overbought_rsi_neutral_trend",
                                "us_close": self.us_close_price,
                                "current_open": ctx.bar.open,
                                "gap_pct": gap_to_current_open_pct,
                                "continuation_pct": continuation_pct,
                                "rsi": rsi,
                                "sma": sma,
                                "atr": atr,
                                "trend_regime": trend_regime,
                                "fear_greed_index": fear_greed_index,
                                "funding_rate": funding_rate,
                            },
                        )

        # Exit logic
        if ctx.has_position():
            position = ctx.position
            entry_price = position["entry_price"]
            direction = position["direction"]
            
            # Exit if gap is filled or if next US session starts
            gap_filled = False
            if self.us_close_price is not None:
                if direction == "long" and ctx.bar.close >= self.us_close_price:
                    gap_filled = True
                elif direction == "short" and ctx.bar.close <= self.us_close_price:
                    gap_filled = True

            # Exit before next US session (13:00 UTC) if position is still open
            is_pre_us_session = current_hour == 12

            if gap_filled or is_pre_us_session:
                self.last_signal_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "gap_filled" if gap_filled else "pre_us_session_exit",
                        "entry_price": entry_price,
                        "current_price": ctx.bar.close,
                        "us_close": self.us_close_price,
                        "gap_filled": gap_filled,
                        "is_pre_us_session": is_pre_us_session,
                    },
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: adaptive logic based on position close results
        pass