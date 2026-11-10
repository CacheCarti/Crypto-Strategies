from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentum(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours (session duration)
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.last_exit_bar = -20
        self.cooldown_bars = 4

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()
        bar_idx = ctx.bar_index

        # Mandatory hard session close: exit by UTC 21 or later
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "us_session_close",
                        "hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard: must wait after exiting
        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Opening window of US market (UTC 13-15)
        if not (13 <= current_hour <= 15):
            return None

        # One trade per calendar day restriction
        if self.last_traded_day == current_day:
            return None

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Measure 12-bar pre-session / session-open momentum relative to volatility
        window = 12
        price_change = closes[-1] - closes[-1 - window]
        pct_change = price_change / closes[-1 - window]

        # Rolling standard deviation of 1-bar returns over 24 bars to normalize drift
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        vol = self._stddev(returns, 20)
        if vol is None or vol == 0:
            return None

        # Standardized move over the 12-bar window
        norm_momentum = pct_change / (vol * math.sqrt(window))

        # Percentile/Z-score threshold: require a clean directional drift (~1 standard deviation move)
        threshold = 1.0

        if norm_momentum > threshold:
            self.last_traded_day = current_day
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.5 + abs(norm_momentum) * 0.15),
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "norm_momentum": round(norm_momentum, 2),
                    "pct_change": round(pct_change * 100, 3),
                    "hour": current_hour
                }
            )
        elif norm_momentum < -threshold:
            self.last_traded_day = current_day
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.5 + abs(norm_momentum) * 0.15),
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "norm_momentum": round(norm_momentum, 2),
                    "pct_change": round(pct_change * 100, 3),
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index