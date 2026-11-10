from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold over the weekend
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_ema = 34
        self.period_rsi = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.traded_this_weekend = False
        self.last_seen_weekend_id = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.period_ema + 5)
        if len(closes) < self.period_ema:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        price = ctx.bar.close

        # Track weekends by ISO calendar week to guarantee at most 1 trade attempt per weekend
        cal = ts.isocalendar()
        current_weekend_id = cal[0] * 100 + cal[1]
        if current_weekend_id != self.last_seen_weekend_id:
            self.last_seen_weekend_id = current_weekend_id
            self.traded_this_weekend = False

        # Regime & filter checks
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # 1. Manage Active Position (Exit on Sunday night or crisis)
        if ctx.has_position():
            # Scheduled weekend exit: Sunday between 20:00 and 23:00 UTC
            is_sunday_exit = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 2)
            is_crisis = regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65

            if is_sunday_exit or is_crisis:
                reason = "crisis_exit" if is_crisis else "weekend_close_target_reached"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "regime": regime,
                        "crisis_score": crisis_score,
                        "price": price,
                    },
                )
            return None

        # 2. Guard against over-trading and cooldown
        if self.traded_this_weekend:
            return None
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.50:
            return None

        # 3. Entry Window: Friday 18:00 UTC through Saturday 04:00 UTC
        is_entry_window = (weekday == 4 and hour >= 18) or (weekday == 5 and hour <= 4)
        if not is_entry_window:
            return None

        # 4. Technical Quality Gate: avoid longing severe downtrends or extreme overbought levels
        ema_val = self._ema(closes, self.period_ema)
        rsi_val = self._rsi(closes, self.period_rsi)
        if ema_val is None or rsi_val is None:
            return None

        # Ensure price is not severely collapsing below trend and not over-exhausted
        is_trend_acceptable = price >= (ema_val * 0.985)
        is_momentum_sound = 38.0 <= rsi_val <= 68.0

        if is_trend_acceptable and is_momentum_sound:
            self.traded_this_weekend = True
            confidence = 0.75 if price > ema_val else 0.60
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_weekend_inflow_expansion",
                    "price": price,
                    "ema34": round(ema_val, 2),
                    "rsi14": round(rsi_val, 2),
                    "weekday": weekday,
                    "hour": hour,
                },
            )

        return None