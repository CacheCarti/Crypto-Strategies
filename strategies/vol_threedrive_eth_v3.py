from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class ThreeDriveReversalEth(Strategy):
    METADATA = {
        "name": "Three Drive Reversal (ETH)",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features used for this pure OHLCV strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drive_period = 5 # Bars to define a "drive"
        self.momentum_period = 10 # Bars for momentum check
        self.cooldown_bars = 10 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

        self.last_closes = [] # Store recent closes for momentum and drive detection
        self.last_highs = []
        self.last_lows = []

        self.drives_history = [] # Stores (bar_index, direction) of detected drives

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _get_momentum(self, closes, period):
        if len(closes) < period:
            return None
        return closes[-1] - closes[-period]

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        # Update historical data
        self.last_closes.append(ctx.bar.close)
        self.last_highs.append(ctx.bar.high)
        self.last_lows.append(ctx.bar.low)
        if len(self.last_closes) > self.warmup_bars + 1:
            self.last_closes.pop(0)
            self.last_highs.pop(0)
            self.last_lows.pop(0)

        # Ensure enough data for indicators
        if len(self.last_closes) < self.warmup_bars:
            return None

        current_close = ctx.bar.close

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Only look for signals if no position is open
        if ctx.has_position():
            return None

        # Detect drives
        is_bull_drive = False
        is_bear_drive = False

        if len(self.last_closes) >= self.drive_period:
            # Check for a bullish drive (price makes higher highs)
            is_bull_drive = True
            for i in range(1, self.drive_period):
                if self.last_highs[-i] <= self.last_highs[-(i+1)]:
                    is_bull_drive = False
                    break

            # Check for a bearish drive (price makes lower lows)
            is_bear_drive = True
            for i in range(1, self.drive_period):
                if self.last_lows[-i] >= self.last_lows[-(i+1)]:
                    is_bear_drive = False
                    break

        # Record drives
        if is_bull_drive:
            if not self.drives_history or self.drives_history[-1][1] != "bull":
                self.drives_history.append((ctx.bar_index, "bull"))
        elif is_bear_drive:
            if not self.drives_history or self.drives_history[-1][1] != "bear":
                self.drives_history.append((ctx.bar_index, "bear"))

        # Keep drives history to a reasonable length
        self.drives_history = [d for d in self.drives_history if d[0] > ctx.bar_index - (self.drive_period * 5)]

        # Check for 3-drive pattern
        recent_bull_drives = [d for d in self.drives_history if d[1] == "bull"]
        recent_bear_drives = [d for d in self.drives_history if d[1] == "bear"]

        # Long signal: three bearish drives, weakening momentum, and current bar fails to make new low
        if len(recent_bear_drives) >= 3:
            # Ensure the drives are somewhat recent and distinct
            if recent_bear_drives[-1][0] > ctx.bar_index - (self.drive_period * 3) and \
               recent_bear_drives[-2][0] > ctx.bar_index - (self.drive_period * 6) and \
               recent_bear_drives[-3][0] > ctx.bar_index - (self.drive_period * 9):

                # Check momentum of the last two drives (from end of 2nd to end of 3rd)
                # Momentum is weakening if the last drive is less powerful than the previous one
                # Approx. price change for the last two drives
                drive2_start_bar = recent_bear_drives[-2][0] - self.drive_period + 1
                drive3_start_bar = recent_bear_drives[-1][0] - self.drive_period + 1

                if drive2_start_bar >= 0 and drive3_start_bar >= 0:
                    drive2_momentum = self.last_closes[recent_bear_drives[-2][0] - ctx.bar_index - 1] - self.last_closes[drive2_start_bar - ctx.bar_index - 1]
                    drive3_momentum = self.last_closes[recent_bear_drives[-1][0] - ctx.bar_index - 1] - self.last_closes[drive3_start_bar - ctx.bar_index - 1]

                    # Check for weakening momentum (less negative momentum)
                    if drive3_momentum > drive2_momentum: # e.g. -5 vs -10 (less negative is weaker bearish momentum)
                        # Current bar fails to make a new low
                        if ctx.bar.low > self.last_lows[-2]: # current low is higher than previous low
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.75,
                                metadata={
                                    "reason": "three_bear_drive_reversal",
                                    "current_low": ctx.bar.low,
                                    "previous_low": self.last_lows[-2],
                                    "drive3_momentum": drive3_momentum,
                                    "drive2_momentum": drive2_momentum,
                                    "regime": ctx.regime,
                                }
                            )

        # Short signal: three bullish drives, weakening momentum, and current bar fails to make new high
        if len(recent_bull_drives) >= 3:
            # Ensure the drives are somewhat recent and distinct
            if recent_bull_drives[-1][0] > ctx.bar_index - (self.drive_period * 3) and \
               recent_bull_drives[-2][0] > ctx.bar_index - (self.drive_period * 6) and \
               recent_bull_drives[-3][0] > ctx.bar_index - (self.drive_period * 9):

                # Check momentum of the last two drives (from end of 2nd to end of 3rd)
                drive2_start_bar = recent_bull_drives[-2][0] - self.drive_period + 1
                drive3_start_bar = recent_bull_drives[-1][0] - self.drive_period + 1

                if drive2_start_bar >= 0 and drive3_start_bar >= 0:
                    drive2_momentum = self.last_closes[recent_bull_drives[-2][0] - ctx.bar_index - 1] - self.last_closes[drive2_start_bar - ctx.bar_index - 1]
                    drive3_momentum = self.last_closes[recent_bull_drives[-1][0] - ctx.bar_index - 1] - self.last_closes[drive3_start_bar - ctx.bar_index - 1]

                    # Check for weakening momentum (less positive momentum)
                    if drive3_momentum < drive2_momentum: # e.g. 5 vs 10 (less positive is weaker bullish momentum)
                        # Current bar fails to make a new high
                        if ctx.bar.high < self.last_highs[-2]: # current high is lower than previous high
                            self.last_trade_bar = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.75,
                                metadata={
                                    "reason": "three_bull_drive_reversal",
                                    "current_high": ctx.bar.high,
                                    "previous_high": self.last_highs[-2],
                                    "drive3_momentum": drive3_momentum,
                                    "drive2_momentum": drive2_momentum,
                                    "regime": ctx.regime,
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy based on position close
        pass