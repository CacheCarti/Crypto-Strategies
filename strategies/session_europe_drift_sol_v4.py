from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEuropeDrift(Strategy):
    METADATA = {
        "name": "Solana Europe Drift",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2 hours hold, roughly the session length
        "warmup_bars": 50,
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7  # UTC 7 AM
        self.entry_hour_end = 12   # UTC 12 PM (exclusive)
        self.exit_hour = 12        # UTC 12 PM
        self.min_session_move_factor = 0.005 # 0.5% move required
        self.min_range_period = 20 # Period for median range calculation
        self.cooldown_bars = 5 # Bars to wait after a trade before entering again
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Parameters for regime gating
        self.ema_period_fast = 10
        self.ema_period_slow = 30
        self.volatility_period = 20
        self.max_crisis_score = 0.5 # Do not trade if crisis score is too high

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _median_range(self, highs, lows, period):
        if len(highs) < period:
            return None
        ranges = [h - l for h, l in zip(highs[-period:], lows[-period:])]
        ranges.sort()
        mid = len(ranges) // 2
        if len(ranges) % 2 == 0:
            return (ranges[mid - 1] + ranges[mid]) / 2
        else:
            return ranges[mid]

    def _volatility(self, closes, period):
        if len(closes) < period:
            return None
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        if len(log_returns) < period:
            return None
        recent_returns = log_returns[-period:]
        mean = sum(recent_returns) / len(recent_returns)
        variance = sum([(x - mean) ** 2 for x in recent_returns]) / len(recent_returns)
        return math.sqrt(variance) * math.sqrt(252) # Annualized volatility approximation

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        opens = ctx.opens(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)

        if len(closes) < self.warmup_bars + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # --- Regime Gating ---
        # Trend filter
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        if ema_fast is None or ema_slow is None:
            return None

        trend_bullish = ema_fast > ema_slow
        trend_bearish = ema_fast < ema_slow

        # Volatility filter (using market context for simplicity)
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Do not trade if crisis score is too high
        if crisis_score > self.max_crisis_score:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # --- Entry Logic ---
        if not ctx.has_position():
            # Check if within European morning session for entry
            if self.entry_hour_start <= current_hour < self.entry_hour_end:
                # Find the open of the current session (first bar within self.entry_hour_start)
                session_open_price = None
                session_high = ctx.bar.high
                session_low = ctx.bar.low
                
                # Iterate backwards to find the session's open
                for i in range(len(ctx.opens(self.entry_hour_end - self.entry_hour_start + 2)) -1, -1, -1):
                    bar_time = ctx.bar.timestamp - (ctx.bar_index - (len(closes) - 1 - i)) * (ctx.bar.timestamp - ctx.closes.timestamps[-2])
                    if bar_time.hour == self.entry_hour_start:
                        session_open_price = ctx.opens(self.entry_hour_end - self.entry_hour_start + 2)[i]
                        break

                if session_open_price is None: # Not enough history for session open
                    return None
                
                # Calculate the session's current movement
                session_move = ctx.bar.close - session_open_price
                session_pct_move = session_move / session_open_price

                # Calculate recent median true range to gauge "decisive" move
                median_tr = self._median_range(highs, lows, self.min_range_period)
                if median_tr is None:
                    return None

                # Ensure the move is decisive (e.g., > 0.5% and exceeds median range)
                is_decisive_long = session_pct_move > self.min_session_move_factor and session_move > median_tr
                is_decisive_short = session_pct_move < -self.min_session_move_factor and abs(session_move) > median_tr

                # Avoid single-bar spikes: check if the current bar's move is disproportionately large
                # compared to the overall session move so far.
                # This is a simplification; a more robust check would involve comparing current bar range
                # to average bar range within the session or overall.
                current_bar_move = ctx.bar.close - ctx.bar.open
                is_spike_long = current_bar_move > 0 and current_bar_move > 0.8 * session_move # If current bar accounts for >80% of session move
                is_spike_short = current_bar_move < 0 and current_bar_move < 0.8 * session_move # If current bar accounts for >80% of session move

                # Long Entry Conditions
                if is_decisive_long and not is_spike_long and trend_bullish and market_regime != "CRISIS":
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "europe_drift_long_bullish_trend",
                            "session_open": session_open_price,
                            "session_move_pct": session_pct_move,
                            "median_tr": median_tr,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "regime": market_regime,
                            "entry_hour": current_hour
                        }
                    )

                # Short Entry Conditions
                elif is_decisive_short and not is_spike_short and trend_bearish and market_regime != "CRISIS":
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "europe_drift_short_bearish_trend",
                            "session_open": session_open_price,
                            "session_move_pct": session_pct_move,
                            "median_tr": median_tr,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "regime": market_regime,
                            "entry_hour": current_hour
                        }
                    )

        # --- Exit Logic ---
        elif ctx.has_position():
            # Exit by UTC 12:00 PM if still in position
            if current_hour >= self.exit_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "session_end_time_exit",
                        "exit_hour": current_hour,
                        "entry_price": ctx.position["entry_price"],
                        "current_price": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position close for analysis, or adapt strategy if needed
        pass