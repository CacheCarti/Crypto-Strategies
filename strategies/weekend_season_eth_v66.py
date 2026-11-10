from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -999
        self.cooldown_bars = 24
        self.entered_this_weekend = False
        self.last_seen_weekend_id = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Friday is 4, Sunday is 6
        hour = ts.hour
        # Unique identifier for the weekend: year and ISO week
        year, week, _ = ts.isocalendar()
        weekend_id = year * 100 + week

        if weekend_id != self.last_seen_weekend_id:
            self.last_seen_weekend_id = weekend_id
            self.entered_this_weekend = False

        current_price = ctx.bar.close
        has_pos = ctx.has_position()

        # 1. Manage Exits if in position
        if has_pos:
            # Exit late Sunday (20:00 to 23:00 UTC) or early Monday
            if (weekday == 6 and hour >= 20) or weekday == 0:
                rsi_val = self._rsi(closes, 14) or 50.0
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_window_closed",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "price": current_price,
                    },
                )
            return None

        # 2. Check Cooldown & Entry Gate
        if self.entered_this_weekend:
            return None

        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # 3. Check Seasonal Entry Window: Friday 18:00 UTC to 23:00 UTC
        if weekday == 4 and (18 <= hour <= 23):
            # Market context regime safety
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            ema24 = self._ema(closes, 24)
            rsi = self._rsi(closes, 14)
            if ema24 is None or rsi is None:
                return None

            # Mild momentum confirmation to avoid catching falling knives
            # ETH should not be drastically crashing (RSI > 38) and should trade near or above EMA
            if rsi >= 38.0 and current_price >= ema24 * 0.985:
                fng = ctx.features.get("fear_greed_index", 50.0)
                # Boost confidence if sentiment isn't overheated
                base_conf = 0.65
                if fng < 65:
                    base_conf += 0.10

                self.last_trade_bar = ctx.bar_index
                self.entered_this_weekend = True

                return ctx.signal(
                    "long",
                    confidence=min(0.9, base_conf),
                    stop_loss_bps=550.0,
                    take_profit_bps=750.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_evening_weekend_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema24": round(ema24, 2),
                        "price": current_price,
                        "fear_greed": fng,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index