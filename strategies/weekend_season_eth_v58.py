from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 24
        self.traded_this_weekend = False
        self.last_seen_weekend_id = -1

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        bar_idx = ctx.bar_index

        # Event week identifier (year and ISO calendar week)
        cal_iso = ts.isocalendar()
        current_weekend_id = cal_iso[0] * 100 + cal_iso[1]
        if current_weekend_id != self.last_seen_weekend_id:
            self.last_seen_weekend_id = current_weekend_id
            self.traded_this_weekend = False

        price = ctx.bar.close
        ema30 = self._ema(closes, 30)
        rsi = self._rsi(closes, 14)

        if ema30 is None or rsi is None:
            return None

        # Manage open position
        if ctx.has_position():
            # Mandatory exit late Sunday (>= 20:00 UTC) or early Monday
            is_sunday_exit = (weekday == 6 and hour >= 20)
            is_monday_exit = (weekday == 0 and hour < 4)
            is_overbought_cut = (rsi > 78.0)

            if is_sunday_exit or is_monday_exit or is_overbought_cut:
                reason = "weekend_close_target" if not is_overbought_cut else "overbought_cut"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "price": price,
                    }
                )
            return None

        # Entry logic: Cooldown and single-trade-per-weekend safeguard
        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.traded_this_weekend:
            return None

        # Friday entry window: between 16:00 and 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Market stress filter: do not catch falling knives in severe crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Price-based confirmation: trend integrity and avoid entering overbought
        # Must be consolidating or trending mildly above EMA30 with healthy RSI
        if price >= ema30 * 0.985 and 35.0 <= rsi <= 65.0:
            fg_index = ctx.features.get("fear_greed_index", 50.0)
            
            # Confidence scaled with market sentiment and trend alignment
            confidence = 0.65
            if price > ema30:
                confidence += 0.1
            if fg_index > 40.0:
                confidence += 0.05
            confidence = min(0.9, confidence)

            self.traded_this_weekend = True
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=450.0,
                take_profit_bps=750.0,
                horizon_seconds=172800,
                metadata={
                    "reason": "friday_weekend_expansion_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema30": round(ema30, 2),
                    "fear_greed": fg_index,
                    "price": price,
                }
            )

        return None