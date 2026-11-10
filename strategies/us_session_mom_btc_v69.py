from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionContinuation(Strategy):
    METADATA = {
        "name": "US Session Momentum Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold inside US window
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.last_trade_day = -1
        self.cooldown_until_bar = 0

    def _stddev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        var = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        current_time = ctx.bar.timestamp
        current_day = current_time.toordinal()
        current_hour = current_time.hour
        in_position = ctx.has_position()

        # Hard exit before UTC 21 or outside operating hours
        if in_position and current_hour >= 21:
            self.cooldown_until_bar = ctx.bar_index + 4
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={"reason": "session_close_time_reached", "hour": current_hour}
            )

        # Do not enter outside US session opening window (UTC 13 to 15)
        if not (13 <= current_hour <= 15):
            return None

        # Respect cooldown and strictly 1 trade per calendar day
        if ctx.bar_index < self.cooldown_until_bar or self.last_trade_day == current_day or in_position:
            return None

        # Check market regime to avoid entering during extreme crisis shocks
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Pre-session 12-bar momentum normalized by realized bar-to-bar volatility
        recent_closes = closes[-self.lookback:]
        net_ret = (recent_closes[-1] - recent_closes[0]) / recent_closes[0]

        step_returns = [
            (recent_closes[i] - recent_closes[i-1]) / recent_closes[i-1]
            for i in range(1, len(recent_closes))
        ]
        ret_vol = self._stddev(step_returns)
        if ret_vol == 0:
            return None

        # Volatility-standardized momentum score across pre-session bars
        t_score = (net_ret / (ret_vol * math.sqrt(self.lookback)))

        # Alignment filter with macro sentiment to avoid fighting strong headwinds
        fg = ctx.features.get("fear_greed_index", 50)

        # Long continuation: significant upward drift without extreme greed exhaustion
        if t_score > 1.25 and fg < 80:
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 6
            conf = min(0.9, max(0.5, 0.5 + (t_score - 1.25) * 0.15))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bull_momentum",
                    "t_score": round(t_score, 2),
                    "net_ret_bps": round(net_ret * 10000, 1),
                    "fg": fg,
                    "hour": current_hour
                }
            )

        # Short continuation: significant downward drift without extreme fear exhaustion
        if t_score < -1.25 and fg > 20:
            self.last_trade_day = current_day
            self.cooldown_until_bar = ctx.bar_index + 6
            conf = min(0.9, max(0.5, 0.5 + (-t_score - 1.25) * 0.15))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "us_session_bear_momentum",
                    "t_score": round(t_score, 2),
                    "net_ret_bps": round(net_ret * 10000, 1),
                    "fg": fg,
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 3