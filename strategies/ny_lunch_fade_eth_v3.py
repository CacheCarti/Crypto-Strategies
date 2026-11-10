from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class IntradayLiquidityPauseFade(Strategy):
    METADATA = {
        "name": "Intraday Liquidity Pause Fade ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3 * 3600, # Aim to hold for 3 hours max
        "warmup_bars": 50, # Need enough bars for 20-period indicators and prior bar checks
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 20
        self.volume_ma_period = 20
        self.last_signal_bar = -100 # Cooldown period

        # Parameters for morning move detection
        self.morning_start_hour_utc = 13
        self.morning_end_hour_utc = 16
        self.min_move_pct = 0.5 # Minimum 0.5% move in the morning
        self.move_std_multiplier = 1.5 # How many std dev from avg move to consider "unusual"

        # Parameters for fading window
        self.fade_start_hour_utc = 16
        self.fade_end_hour_utc = 19

        # Maximum hold time (exit by this hour)
        self.max_hold_exit_hour_utc = 21

        # State to track for transition detection and cooldown
        self.morning_move_direction = None # "long" or "short"
        self.morning_move_start_price = None
        self.morning_move_end_price = None
        self.morning_move_magnitude = None # Absolute magnitude of the move

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        opens = ctx.opens(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # --- Cooldown Logic ---
        if ctx.bar_index - self.last_signal_bar < 5: # 5-bar cooldown
            return None

        # --- Regime Filters ---
        fear_greed = ctx.features.get("fear_greed_index", 50)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Skip crisis regimes and extreme fear/greed
        if market_regime in ["CRISIS", "MELTDOWN"] or fear_greed < 20 or fear_greed > 80:
            return None

        # Skip if already in position
        if ctx.has_position():
            # Check for max hold time exit
            if ctx.position_direction() == "long" and current_hour == self.max_hold_exit_hour_utc:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "max_hold_time_exit", "entry_hour": ctx.position["entry_bar_timestamp"].hour, "current_hour": current_hour})
            elif ctx.position_direction() == "short" and current_hour == self.max_hold_exit_hour_utc:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "max_hold_time_exit", "entry_hour": ctx.position["entry_bar_timestamp"].hour, "current_hour": current_hour})

            # Check for 50% retracement of morning move (only if we have a morning move recorded)
            if self.morning_move_magnitude and self.morning_move_start_price:
                if ctx.position_direction() == "long" and self.morning_move_direction == "short": # Fading a short move
                    # Price should have recovered by 50% of the morning move
                    target_price = self.morning_move_end_price + (self.morning_move_magnitude / 2)
                    if current_close >= target_price:
                        self.morning_move_direction = None # Reset for next day
                        return ctx.signal("flat", confidence=0.8, metadata={"reason": "fade_retracement_exit", "morning_move_dir": "short", "retracement_target": target_price, "current_close": current_close})
                elif ctx.position_direction() == "short" and self.morning_move_direction == "long": # Fading a long move
                    # Price should have retraced by 50% of the morning move
                    target_price = self.morning_move_end_price - (self.morning_move_magnitude / 2)
                    if current_close <= target_price:
                        self.morning_move_direction = None # Reset for next day
                        return ctx.signal("flat", confidence=0.8, metadata={"reason": "fade_retracement_exit", "morning_move_dir": "long", "retracement_target": target_price, "current_close": current_close})

            return None # Hold position if no exit condition met

        # --- Morning Move Detection (UTC 13-16) ---
        if current_hour == self.morning_start_hour_utc: # Start of morning period
            self.morning_move_start_price = opens[-1] # Price at the start of the 13:00 bar
            self.morning_move_direction = None
            self.morning_move_magnitude = None
            self.morning_move_end_price = None

        if current_hour == self.morning_end_hour_utc: # End of morning period (after 16:00 bar closes)
            if self.morning_move_start_price is not None:
                move_end_price = closes[-1]
                move = move_end_price - self.morning_move_start_price
                move_pct = (move / self.morning_move_start_price) * 100

                # Calculate average and std dev of previous day's morning moves (proxy using 20 bars)
                # This is a simplification; in a real system, we'd store daily moves.
                # Here, we look at the volatility of the last X bars as a proxy for "usual" move.
                # A more robust solution would track actual daily moves.
                recent_moves = [(closes[i] - opens[i]) / opens[i] * 100 for i in range(len(opens) - self.slow_ema_period, len(opens))]
                if len(recent_moves) == self.slow_ema_period:
                    avg_recent_move = sum(recent_moves) / len(recent_moves)
                    std_recent_move = self._stddev(recent_moves, self.slow_ema_period)
                    if std_recent_move is not None and std_recent_move > 0:
                        # Check if the current move is "unusually one-sided"
                        if move_pct > self.min_move_pct and move_pct > (avg_recent_move + self.move_std_multiplier * std_recent_move):
                            self.morning_move_direction = "long"
                            self.morning_move_magnitude = abs(move)
                            self.morning_move_end_price = move_end_price
                        elif move_pct < -self.min_move_pct and move_pct < (avg_recent_move - self.move_std_multiplier * std_recent_move):
                            self.morning_move_direction = "short"
                            self.morning_move_magnitude = abs(move)
                            self.morning_move_end_price = move_end_price
                    else: # If std dev is zero or very small, just check against min_move_pct
                        if move_pct > self.min_move_pct:
                            self.morning_move_direction = "long"
                            self.morning_move_magnitude = abs(move)
                            self.morning_move_end_price = move_end_price
                        elif move_pct < -self.min_move_pct:
                            self.morning_move_direction = "short"
                            self.morning_move_magnitude = abs(move)
                            self.morning_move_end_price = move_end_price

        # --- Fade Logic (UTC 16-19) ---
        if self.fade_start_hour_utc <= current_hour < self.fade_end_hour_utc and self.morning_move_direction:
            # Check for volume confirmation (low volume in fade period)
            current_volume = ctx.bar.volume
            avg_volume = self._sma(volumes, self.volume_ma_period)

            if avg_volume is None or current_volume > avg_volume * 0.8: # Require volume to be relatively lower
                return None

            # Calculate EMAs for trend confirmation
            fast_ema = self._ema(closes, self.fast_ema_period)
            slow_ema = self._ema(closes, self.slow_ema_period)

            if fast_ema is None or slow_ema is None:
                return None

            signal_confidence = 0.65 # Base confidence

            if self.morning_move_direction == "long": # Morning was a strong up-move, look to short
                # Entry condition: price starts to dip below fast EMA, and fast EMA is below slow EMA (or crossing down)
                if current_close < fast_ema and fast_ema < slow_ema and closes[-2] > fast_ema: # Cross down
                    self.last_signal_bar = ctx.bar_index
                    self.morning_move_direction = None # Reset to prevent repeated signals for the same move
                    return ctx.signal(
                        "short",
                        confidence=signal_confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "intraday_liquidity_pause_fade_short",
                            "morning_move_direction": "long",
                            "morning_move_magnitude": self.morning_move_magnitude,
                            "current_close": current_close,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "volume_ratio": current_volume / avg_volume,
                            "fear_greed": fear_greed
                        }
                    )
            elif self.morning_move_direction == "short": # Morning was a strong down-move, look to long
                # Entry condition: price starts to rise above fast EMA, and fast EMA is above slow EMA (or crossing up)
                if current_close > fast_ema and fast_ema > slow_ema and closes[-2] < fast_ema: # Cross up
                    self.last_signal_bar = ctx.bar_index
                    self.morning_move_direction = None # Reset to prevent repeated signals for the same move
                    return ctx.signal(
                        "long",
                        confidence=signal_confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "intraday_liquidity_pause_fade_long",
                            "morning_move_direction": "short",
                            "morning_move_magnitude": self.morning_move_magnitude,
                            "current_close": current_close,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "volume_ratio": current_volume / avg_volume,
                            "fear_greed": fear_greed
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_signal_bar = ctx.bar_index # Reset cooldown on close
        self.morning_move_direction = None # Ensure morning move state is reset after a trade
        self.morning_move_magnitude = None
        self.morning_move_start_price = None
        self.morning_move_end_price = None