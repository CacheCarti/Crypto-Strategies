from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilitySqueezeBreakout(Strategy):
    METADATA = {
        "name": "Volatility Squeeze Breakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 3600,
        "warmup_bars": 60,  # Need enough bars for Bollinger Bands, ATR, and SMA
        "required_features": [], # No specific features required for this OHLCV-only strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std_dev = 2.0
        self.atr_period = 14
        self.squeeze_lookback = 10  # How many bars to look back for squeeze condition
        self.breakout_confirmation_bars = 2 # How many bars price must stay outside bands
        self.cooldown_bars = 12 # Cooldown after a position closes
        self.last_exit_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        # Squeeze detection thresholds (adjust as needed)
        self.atr_std_dev_threshold = 0.5 # ATR std dev must be below this multiple of its mean
        self.bb_width_compression_ratio = 0.8 # BB width must be compressed by this ratio

        self.last_squeeze_bar = -1 # Track the bar index where a squeeze was detected

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

    def _bollinger(self, closes, period, num_std):
        if len(closes) < period:
            return None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std, std

    def _atr(self, highs, lows, closes, period):
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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_exit_bar + self.cooldown_bars:
            return None

        # Calculate Bollinger Bands and ATR
        bb_middle, bb_upper, bb_lower, bb_std = self._bollinger(closes, self.bb_period, self.bb_std_dev)
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if bb_middle is None or current_atr is None:
            return None

        # Check for volatility squeeze
        # A squeeze is defined by a period of low Bollinger Band width and low ATR
        # compared to their recent historical averages.
        bb_widths = []
        atrs = []
        for i in range(self.squeeze_lookback, len(closes) + 1):
            temp_closes = closes[i - self.squeeze_lookback:i]
            temp_highs = highs[i - self.squeeze_lookback:i]
            temp_lows = lows[i - self.squeeze_lookback:i]

            _, temp_bb_upper, temp_bb_lower, _ = self._bollinger(temp_closes, self.bb_period, self.bb_std_dev)
            if temp_bb_upper is not None and temp_bb_lower is not None:
                bb_widths.append(temp_bb_upper - temp_bb_lower)

            temp_atr = self._atr(temp_highs, temp_lows, temp_closes, self.atr_period)
            if temp_atr is not None:
                atrs.append(temp_atr)

        if len(bb_widths) < self.squeeze_lookback or len(atrs) < self.squeeze_lookback:
            return None

        current_bb_width = bb_upper - bb_lower
        avg_bb_width_recent = self._sma(bb_widths, self.squeeze_lookback)
        avg_atr_recent = self._sma(atrs, self.squeeze_lookback)

        is_squeezing = False
        if avg_bb_width_recent is not None and avg_atr_recent is not None:
            # Check if current width is significantly lower than recent average
            if current_bb_width < avg_bb_width_recent * self.bb_width_compression_ratio:
                # Check if current ATR is significantly lower than recent average
                if current_atr < avg_atr_recent * self.atr_std_dev_threshold:
                    is_squeezing = True

        # If a squeeze is detected, record the bar index
        if is_squeezing and self.last_squeeze_bar < ctx.bar_index - self.squeeze_lookback: # Ensure it's a new squeeze
            self.last_squeeze_bar = ctx.bar_index
            return None # Wait for breakout after squeeze

        # Only look for breakouts if a squeeze has been detected recently
        if self.last_squeeze_bar == -1 or ctx.bar_index - self.last_squeeze_bar > self.squeeze_lookback * 2:
            return None # Squeeze too old or never happened

        # Regime filter: only trade in 'normal' or 'volatile' regimes, and not in 'crisis'
        current_regime = ctx.market.get("regime", "NORMAL")
        if current_regime == "CRISIS" or current_regime == "MELTDOWN":
            return None

        # Breakout confirmation (price stays outside bands for a few bars)
        long_breakout_confirmed = False
        short_breakout_confirmed = False

        if ctx.bar_index >= self.breakout_confirmation_bars:
            long_breakout_confirmed = True
            for i in range(self.breakout_confirmation_bars):
                if ctx.closes(-self.breakout_confirmation_bars)[i] <= bb_upper:
                    long_breakout_confirmed = False
                    break

            short_breakout_confirmed = True
            for i in range(self.breakout_confirmation_bars):
                if ctx.closes(-self.breakout_confirmation_bars)[i] >= bb_lower:
                    short_breakout_confirmed = False
                    break

        # Entry logic
        if not ctx.has_position():
            if long_breakout_confirmed and ctx.bar.close > bb_upper:
                confidence = 0.7 + ctx.rng.uniform(0, 0.1) # Add some randomness
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    metadata={
                        "reason": "vol_squeeze_long_breakout",
                        "current_price": ctx.bar.close,
                        "bb_upper": bb_upper,
                        "bb_lower": bb_lower,
                        "bb_width_ratio": current_bb_width / avg_bb_width_recent if avg_bb_width_recent else "N/A",
                        "atr_ratio": current_atr / avg_atr_recent if avg_atr_recent else "N/A",
                        "regime": current_regime,
                        "last_squeeze_bar_diff": ctx.bar_index - self.last_squeeze_bar
                    }
                )
            elif short_breakout_confirmed and ctx.bar.close < bb_lower:
                confidence = 0.7 + ctx.rng.uniform(0, 0.1) # Add some randomness
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    metadata={
                        "reason": "vol_squeeze_short_breakout",
                        "current_price": ctx.bar.close,
                        "bb_upper": bb_upper,
                        "bb_lower": bb_lower,
                        "bb_width_ratio": current_bb_width / avg_bb_width_recent if avg_bb_width_recent else "N/A",
                        "atr_ratio": current_atr / avg_atr_recent if avg_atr_recent else "N/A",
                        "regime": current_regime,
                        "last_squeeze_bar_diff": ctx.bar_index - self.last_squeeze_bar
                    }
                )
        else:
            # Exit logic (can be expanded with more conditions like mean reversion after breakout)
            pass

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last squeeze bar to ensure a new squeeze is detected before the next trade
        # This helps prevent re-entering too quickly on a failed breakout or chop
        self.last_squeeze_bar = -1
        self.last_exit_bar = ctx.bar_index

        # Log position details for analysis
        ctx.log(f"Position closed: {position['direction']} at {position['exit_price']}. "
                f"Return: {position['return_bps']:.2f} bps, Held: {position['bars_held']} bars. "
                f"Reason: {position.get('exit_reason', 'unknown')}")