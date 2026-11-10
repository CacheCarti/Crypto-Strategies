from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.min_ret_bps = 65.0       # Minimum momentum threshold to avoid chop
        self.max_ret_bps = 500.0      # Cap overextended moves
        self.last_trade_day = -1
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 15:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.timetuple().tm_yday

        # Hard session exit: force flat at or after UTC 21
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_utc21", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Check entry window: only at start of US session (UTC 13 to 15)
        if current_hour < 13 or current_hour > 15:
            return None

        # 1 trade per day rule and cooldown guard
        if current_day == self.last_trade_day:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Measure 12-bar momentum (overnight/Asian/London lead-up)
        past_price = closes[-self.lookback - 1]
        curr_price = closes[-1]
        if past_price <= 0:
            return None

        ret_bps = ((curr_price - past_price) / past_price) * 10000.0
        abs_ret = abs(ret_bps)

        # Require meaningful directional thrust, but not an exhausted blowout
        if abs_ret < self.min_ret_bps or abs_ret > self.max_ret_bps:
            return None

        # Dynamic volatility check: ensure move exceeds 1.0x 14-bar ATR
        highs = ctx.highs(25)
        lows = ctx.lows(25)
        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val <= 0:
            return None
        
        move_size = abs(curr_price - past_price)
        if move_size < atr_val * 0.9:
            return None

        # Moderate sentiment filter to avoid fighting extreme panic/euphoria
        fg_index = ctx.features.get("fear_greed_index", 50)
        
        confidence = min(0.9, 0.55 + (abs_ret / 1000.0))

        if ret_bps > 0 and fg_index >= 25:
            self.last_trade_day = current_day
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_continuation_long",
                    "ret_bps": round(ret_bps, 1),
                    "atr": round(atr_val, 1),
                    "fg_index": fg_index,
                    "hour": current_hour
                }
            )

        elif ret_bps < 0 and fg_index <= 75:
            self.last_trade_day = current_day
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_continuation_short",
                    "ret_bps": round(ret_bps, 1),
                    "atr": round(atr_val, 1),
                    "fg_index": fg_index,
                    "hour": current_hour
                }
            )

        return None