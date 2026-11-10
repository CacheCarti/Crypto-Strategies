from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours (session hold)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.session_open_hour = 13
        self.session_close_hour = 21
        self.last_trade_day = None
        self.cooldown_until_bar = 0

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Enforce cooldown of 3 bars after position close to avoid re-entry churn
        self.cooldown_until_bar = ctx.bar_index + 3

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = (current_time.year, current_time.month, current_time.day)

        # Mandatory session exit: close positions entering or after UTC 21
        if ctx.has_position():
            if current_hour >= self.session_close_hour or current_hour < self.session_open_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "session_close_utc21",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )

        # Skip entries if currently in position, in cooldown, or outside the session entry window
        if ctx.has_position():
            return None

        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Entry window: Evaluate between 13:00 and 15:00 UTC, max 1 trade per day
        if not (self.session_open_hour <= current_hour <= 15):
            return None

        if self.last_trade_day == current_day:
            return None

        # Compute prior 12-bar momentum (overnight & European session move) normalized by volatility
        pct_change = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        
        # Volatility normalization: 12-bar standard deviation of returns
        recent_returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(len(closes) - self.lookback, len(closes))
        ]
        mean_ret = sum(recent_returns) / len(recent_returns)
        var = sum((r - mean_ret) ** 2 for r in recent_returns) / len(recent_returns)
        vol = math.sqrt(var) if var > 0 else 0.001

        # Normalized momentum z-score across the session lead-up
        mom_z = pct_change / (vol * math.sqrt(self.lookback))

        # Regime & macro alignment
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # Skip entries during severe market crises
        if crisis_score > 0.7:
            return None

        # Normalized momentum threshold: absolute z-score >= 1.0 (clear directional drive)
        # Avoids magic exact dollar/bps thresholds by measuring relative move vs recent dispersion
        if mom_z > 1.0 and fg_index > 25:
            self.last_trade_day = current_day
            confidence = min(0.9, 0.5 + 0.15 * min(mom_z, 2.5))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bull_continuation",
                    "mom_z": round(mom_z, 2),
                    "pct_change": round(pct_change * 100, 2),
                    "fg_index": fg_index,
                    "hour": current_hour,
                }
            )

        if mom_z < -1.0 and fg_index < 75:
            self.last_trade_day = current_day
            confidence = min(0.9, 0.5 + 0.15 * min(abs(mom_z), 2.5))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_bear_continuation",
                    "mom_z": round(mom_z, 2),
                    "pct_change": round(pct_change * 100, 2),
                    "fg_index": fg_index,
                    "hour": current_hour,
                }
            )

        return None