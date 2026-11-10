from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ThreeDriveExhaustion(Strategy):
    METADATA = {
        "name": "Three Drive Exhaustion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Intended avg hold time is a few hours
        "warmup_bars": 60,  # Need enough bars for 3 drives and momentum checks
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drive_period = 5  # Lookback for a "drive"
        self.momentum_period = 10 # Period for momentum calculation
        self.cooldown_bars = 12  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade
        self.min_drive_length_pct = 0.5 # Minimum percentage gain/loss for a drive
        self.max_drive_overlap_pct = 0.2 # Max overlap between drives

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _momentum(self, closes, period):
        if len(closes) < period + 1:
            return None
        return closes[-1] - closes[-period-1]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        # Check for 3-drive exhaustion pattern
        # A "drive" is a sustained move in one direction over self.drive_period bars
        # Momentum is weakening if the current drive is shorter than the previous one
        
        # Look for bullish 3-drive (bearish signal)
        if not ctx.has_position():
            # Drive 1: upwards move
            drive1_start_idx = len(closes) - 3 * self.drive_period - 1
            if drive1_start_idx < 0:
                return None
            
            drive1_low = min(lows[drive1_start_idx : drive1_start_idx + self.drive_period])
            drive1_high = max(highs[drive1_start_idx : drive1_start_idx + self.drive_period])
            drive1_length = drive1_high - drive1_low
            drive1_momentum = self._momentum(closes[drive1_start_idx : drive1_start_idx + self.drive_period + self.momentum_period], self.momentum_period)

            # Drive 2: upwards move, after a small pullback
            drive2_start_idx = len(closes) - 2 * self.drive_period - 1
            drive2_low = min(lows[drive2_start_idx : drive2_start_idx + self.drive_period])
            drive2_high = max(highs[drive2_start_idx : drive2_start_idx + self.drive_period])
            drive2_length = drive2_high - drive2_low
            drive2_momentum = self._momentum(closes[drive2_start_idx : drive2_start_idx + self.drive_period + self.momentum_period], self.momentum_period)

            # Drive 3: upwards move, after a small pullback
            drive3_start_idx = len(closes) - self.drive_period - 1
            drive3_low = min(lows[drive3_start_idx : drive3_start_idx + self.drive_period])
            drive3_high = max(highs[drive3_start_idx : drive3_start_idx + self.drive_period])
            drive3_length = drive3_high - drive3_low
            drive3_momentum = self._momentum(closes[drive3_start_idx : drive3_start_idx + self.drive_period + self.momentum_period], self.momentum_period)

            # Ensure all drives are valid and in the correct direction
            is_bullish_drive1 = drive1_length > 0 and (drive1_high - closes[drive1_start_idx]) / closes[drive1_start_idx] * 100 > self.min_drive_length_pct
            is_bullish_drive2 = drive2_length > 0 and (drive2_high - closes[drive2_start_idx]) / closes[drive2_start_idx] * 100 > self.min_drive_length_pct
            is_bullish_drive3 = drive3_length > 0 and (drive3_high - closes[drive3_start_idx]) / closes[drive3_start_idx] * 100 > self.min_drive_length_pct

            # Check for decreasing momentum and structure
            if (is_bullish_drive1 and is_bullish_drive2 and is_bullish_drive3 and
                drive3_high > drive2_high and drive2_high > drive1_high and
                drive3_length < drive2_length and drive2_length < drive1_length and
                drive3_momentum < 0 and drive2_momentum < drive1_momentum and
                current_price < drive3_high - drive3_length * 0.5): # Fade the first failed push (drop below midpoint of last drive)

                self.last_trade_bar = ctx.bar_index
                # Exit at midpoint of the third drive or tight structure stop
                # For a short, midpoint exit is below current price, so use as TP.
                # SL is typically above the peak of the third drive
                take_profit_price = drive3_low + drive3_length * 0.5
                stop_loss_price = drive3_high + (drive3_high - drive3_low) * 0.2 # 20% of drive length above peak

                declared_sl_bps = (stop_loss_price - current_price) / current_price * 10000
                declared_tp_bps = (current_price - take_profit_price) / current_price * 10000

                return ctx.signal(
                    "short",
                    confidence=0.7,
                    stop_loss_bps=declared_sl_bps,
                    take_profit_bps=declared_tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "three_drive_bullish_exhaustion_fade",
                        "drive1_high": drive1_high, "drive1_low": drive1_low, "drive1_length": drive1_length, "drive1_momentum": drive1_momentum,
                        "drive2_high": drive2_high, "drive2_low": drive2_low, "drive2_length": drive2_length, "drive2_momentum": drive2_momentum,
                        "drive3_high": drive3_high, "drive3_low": drive3_low, "drive3_length": drive3_length, "drive3_momentum": drive3_momentum,
                        "current_price": current_price,
                        "midpoint_target": take_profit_price,
                        "stop_loss_price": stop_loss_price,
                    },
                )

            # Look for bearish 3-drive (bullish signal)
            drive1_start_idx = len(closes) - 3 * self.drive_period - 1
            if drive1_start_idx < 0:
                return None
            
            drive1_low = min(lows[drive1_start_idx : drive1_start_idx + self.drive_period])
            drive1_high = max(highs[drive1_start_idx : drive1_start_idx + self.drive_period])
            drive1_length = drive1_high - drive1_low
            drive1_momentum = self._momentum(closes[drive1_start_idx : drive1_start_idx + self.drive_period + self.momentum_period], self.momentum_period)

            # Drive 2: downwards move, after a small rebound
            drive2_start_idx = len(closes) - 2 * self.drive_period - 1
            drive2_low = min(lows[drive2_start_idx : drive2_start_idx + self.drive_period])
            drive2_high = max(highs[drive2_start_idx : drive2_start_idx + self.drive_period])
            drive2_length = drive2_high - drive2_low
            drive2_momentum = self._momentum(closes[drive2_start_idx : drive2_start_idx + self.drive_period + self.momentum_period], self.momentum_period)

            # Drive 3: downwards move, after a small rebound
            drive3_start_idx = len(closes) - self.drive_period - 1
            drive3_low = min(lows[drive3_start_idx : drive3_start_idx + self.drive_period])
            drive3_high = max(highs[drive3_start_idx : drive3_start_idx + self.drive_period])
            drive3_length = drive3_high - drive3_low
            drive3_momentum = self._momentum(closes[drive3_start_idx : drive3_start_idx + self.drive_period + self.momentum_period], self.momentum_period)

            # Ensure all drives are valid and in the correct direction
            is_bearish_drive1 = drive1_length > 0 and (closes[drive1_start_idx] - drive1_low) / closes[drive1_start_idx] * 100 > self.min_drive_length_pct
            is_bearish_drive2 = drive2_length > 0 and (closes[drive2_start_idx] - drive2_low) / closes[drive2_start_idx] * 100 > self.min_drive_length_pct
            is_bearish_drive3 = drive3_length > 0 and (closes[drive3_start_idx] - drive3_low) / closes[drive3_start_idx] * 100 > self.min_drive_length_pct

            # Check for decreasing momentum and structure
            if (is_bearish_drive1 and is_bearish_drive2 and is_bearish_drive3 and
                drive3_low < drive2_low and drive2_low < drive1_low and
                drive3_length < drive2_length and drive2_length < drive1_length and
                drive3_momentum > 0 and drive2_momentum > drive1_momentum and
                current_price > drive3_low + drive3_length * 0.5): # Fade the first failed push (bounce above midpoint of last drive)

                self.last_trade_bar = ctx.bar_index
                # For a long, midpoint exit is above current price, so use as TP.
                # SL is typically below the trough of the third drive
                take_profit_price = drive3_high - drive3_length * 0.5
                stop_loss_price = drive3_low - (drive3_high - drive3_low) * 0.2 # 20% of drive length below trough

                declared_sl_bps = (current_price - stop_loss_price) / current_price * 10000
                declared_tp_bps = (take_profit_price - current_price) / current_price * 10000

                return ctx.signal(
                    "long",
                    confidence=0.7,
                    stop_loss_bps=declared_sl_bps,
                    take_profit_bps=declared_tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "three_drive_bearish_exhaustion_fade",
                        "drive1_high": drive1_high, "drive1_low": drive1_low, "drive1_length": drive1_length, "drive1_momentum": drive1_momentum,
                        "drive2_high": drive2_high, "drive2_low": drive2_low, "drive2_length": drive2_length, "drive2_momentum": drive2_momentum,
                        "drive3_high": drive3_high, "drive3_low": drive3_low, "drive3_length": drive3_length, "drive3_momentum": drive3_momentum,
                        "current_price": current_price,
                        "midpoint_target": take_profit_price,
                        "stop_loss_price": stop_loss_price,
                    },
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on position close, so a new signal can be generated after cooldown_bars
        self.last_trade_bar = ctx.bar_index
        pass