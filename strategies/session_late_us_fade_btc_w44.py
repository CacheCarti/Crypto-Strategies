from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionFadeStrategy(Strategy):
    METADATA = {
        "name": "BTC Late US Session Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold is ~4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt", "btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_midpoint = None
        self.session_high = -math.inf
        self.session_low = math.inf
        self.trade_cooldown_bars = 0
        self.max_cooldown_bars = 4 # Wait 4 hours after a trade
        self.last_trade_bar = -self.max_cooldown_bars
        self.traded_this_session = False

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
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Daily reset for session logic
        if current_hour == 0:
            self.session_midpoint = None
            self.session_high = -math.inf
            self.session_low = math.inf
            self.traded_this_session = False

        # Update session high/low
        if self.session_midpoint is not None: # Only update if a session has started
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Calculate 20-bar Z-score for volatility check
        z_score_closes = self._zscore(closes, 20)
        if z_score_closes is None:
            return None

        # Market regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Avoid trading during crisis or extreme fear/greed
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"] or \
           fear_greed < 20 or fear_greed > 80:
            return None

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar < self.max_cooldown_bars:
            return None

        # --- Entry Logic: Late US Session Fade (UTC 18-24) ---
        if 18 <= current_hour <= 23: # Late US session
            if self.session_midpoint is None:
                # Define session start (e.g., open of 18:00 UTC bar)
                self.session_midpoint = ctx.bar.open # This bar's open as the initial reference
                self.session_high = ctx.bar.high
                self.session_low = ctx.bar.low
                self.traded_this_session = False
                return None # Wait for some price action to develop

            if self.traded_this_session:
                return None # Only one trade per session

            # Calculate current price deviation from session midpoint
            deviation_from_midpoint = (current_close - self.session_midpoint) / self.session_midpoint * 10000 # in bps

            # Check for stretched move (e.g., 100 bps from midpoint) and stalling (low Z-score indicates consolidation)
            # We want to fade an extended move that is losing momentum
            stretch_threshold_bps = 120 # 1.2% move
            zscore_stalling_threshold = 0.5 # Price not moving much relative to past volatility

            # Don't fade strong trends (e.g. if BTC 24hr return is very high/low)
            if abs(btc_return) > 3.0: # Filter out strong trending days
                return None

            # Long entry: price is significantly below midpoint, but stalling (low z-score)
            if deviation_from_midpoint < -stretch_threshold_bps and abs(z_score_closes) < zscore_stalling_threshold:
                # Additional filter: ensure we are not in a strong downtrend (e.g., funding rate not too negative)
                if funding_rate > -0.0005: # Funding rate not extremely negative
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "late_us_session_fade_long",
                            "session_midpoint": self.session_midpoint,
                            "current_close": current_close,
                            "deviation_bps": deviation_from_midpoint,
                            "z_score_closes": z_score_closes,
                            "fear_greed": fear_greed,
                            "funding_rate": funding_rate
                        }
                    )

            # Short entry: price is significantly above midpoint, but stalling (low z-score)
            elif deviation_from_midpoint > stretch_threshold_bps and abs(z_score_closes) < zscore_stalling_threshold:
                # Additional filter: ensure we are not in a strong uptrend (e.g., funding rate not too positive)
                if funding_rate < 0.0005: # Funding rate not extremely positive
                    self.traded_this_session = True
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "late_us_session_fade_short",
                            "session_midpoint": self.session_midpoint,
                            "current_close": current_close,
                            "deviation_bps": deviation_from_midpoint,
                            "z_score_closes": z_score_closes,
                            "fear_greed": fear_greed,
                            "funding_rate": funding_rate
                        }
                    )

        # --- Exit Logic: Flat before UTC 0 ---
        if ctx.has_position() and current_hour == 23 and ctx.bar.timestamp.minute >= 55: # Exit in the last 5 minutes of the session
            return ctx.signal(
                "flat",
                confidence=0.9,
                metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "current_close": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset any state that should be re-evaluated after a position close
        # In this strategy, the 'traded_this_session' and 'last_trade_bar' are handled per-session/cooldown.
        # This function could be used for adaptive sizing, but for now, it just logs.
        pass