from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEuropeDrift(Strategy):
    METADATA = {
        "name": "SOL Europe Drift",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours, covering the session and some drift
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 10   # UTC 10 AM (allowing entries up to 9:59 AM)
        self.exit_hour = 12        # UTC 12 PM
        self.min_session_move_factor = 0.005 # 0.5% move
        self.median_range_period = 24 # For calculating recent median range
        self.max_single_bar_spike_factor = 0.003 # 0.3%
        self.min_vol_ratio = 1.2 # Volume must be 20% higher than average
        self.volume_avg_period = 20

        self.last_trade_bar = -1
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade

    def _get_median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        # Calculate median of ranges
        sorted_ranges = sorted(ranges)
        mid = len(sorted_ranges) // 2
        return (sorted_ranges[mid - 1] + sorted_ranges[mid]) / 2 if len(sorted_ranges) % 2 == 0 else sorted_ranges[mid]

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        volumes = ctx.volumes(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for existing positions
        if ctx.has_position():
            if current_hour == self.exit_hour and ctx.position_direction() != "flat":
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "exit_hour": self.exit_hour})
            return None # Hold position if not exit hour

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic
        if self.entry_hour_start <= current_hour < self.entry_hour_end:
            # Check for decisive move in the current bar
            current_close = ctx.bar.close
            current_open = ctx.bar.open
            current_volume = ctx.bar.volume

            bar_move_abs = abs(current_close - current_open) / current_open
            
            # Avoid single-bar spikes
            if bar_move_abs > self.max_single_bar_spike_factor:
                 return None

            # Calculate recent median range
            median_range = self._get_median_range(highs, lows, self.median_range_period)
            if median_range is None:
                return None
            
            # Session move must exceed a fraction of the median range
            min_decisive_move = median_range * self.min_session_move_factor
            if bar_move_abs < min_decisive_move:
                return None

            # Volume confirmation
            avg_volume = self._sma(volumes, self.volume_avg_period)
            if avg_volume is None or current_volume < avg_volume * self.min_vol_ratio:
                return None

            # Determine direction based on current bar move
            signal_direction = None
            if current_close > current_open:
                signal_direction = "long"
            elif current_close < current_open:
                signal_direction = "short"

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=0.8,
                    metadata={
                        "reason": f"europe_drift_{signal_direction}",
                        "entry_hour": current_hour,
                        "bar_move_pct": bar_move_abs * 100,
                        "median_range_factor": self.min_session_move_factor,
                        "current_volume": current_volume,
                        "avg_volume": avg_volume,
                        "volume_ratio": current_volume / avg_volume,
                        "close": current_close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy, just log
        pass