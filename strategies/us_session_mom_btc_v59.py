from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomBtc(Strategy):
    METADATA = {
        "name": "US Session Momentum BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,  # Max intraday session hold (~6h)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.entry_bar = -1
        self.cooldown_until_bar = 0

    def _stddev(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Rule 1: Intraday Session Cutoff — flat all positions by UTC 21:00
        if ctx.has_position():
            if current_hour >= 21 or current_hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_cutoff", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Rule 2: Post-exit cooldown & one-trade-per-day restriction
        if ctx.bar_index < self.cooldown_until_bar:
            return None
        if self.last_traded_day == current_day:
            return None

        # Rule 3: Trade only at the US opening window (UTC 13 or 14)
        if current_hour not in (13, 14):
            return None

        # Feature / Market gating
        fg_index = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Adaptive Momentum: Measure 12-bar normalized momentum (rolling Z-score of 12-bar return)
        past_12_ret = (closes[-1] - closes[-13]) / closes[-13]
        
        # Calculate trailing distribution of 12-bar returns to avoid brittle fixed thresholds
        ret_12_series = []
        for i in range(12, len(closes)):
            ret_12_series.append((closes[i] - closes[i - 12]) / closes[i - 12])
        
        if len(ret_12_series) < 10:
            return None

        mean_ret = sum(ret_12_series) / len(ret_12_series)
        variance = sum((r - mean_ret) ** 2 for r in ret_12_series) / len(ret_12_series)
        std_ret = math.sqrt(variance) if variance > 0 else 0.0001
        z_momentum = (past_12_ret - mean_ret) / std_ret

        # Dynamic entry: normalized move >= 1.0 standard deviations
        if z_momentum > 1.0 and fg_index >= 25:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + 8
            confidence = min(0.9, 0.55 + 0.15 * min(z_momentum, 2.5))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_long",
                    "z_momentum": round(z_momentum, 3),
                    "past_12_ret": round(past_12_ret, 4),
                    "fear_greed": fg_index,
                    "hour": current_hour
                }
            )

        elif z_momentum < -1.0 and fg_index <= 75:
            self.last_traded_day = current_day
            self.entry_bar = ctx.bar_index
            self.cooldown_until_bar = ctx.bar_index + 8
            confidence = min(0.9, 0.55 + 0.15 * min(abs(z_momentum), 2.5))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "us_session_open_continuation_short",
                    "z_momentum": round(z_momentum, 3),
                    "past_12_ret": round(past_12_ret, 4),
                    "fear_greed": fg_index,
                    "hour": current_hour
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4