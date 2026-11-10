from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 950.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_week_id = -1

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

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
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        current_dt = ctx.bar.timestamp
        weekday = current_dt.weekday()
        hour = current_dt.hour
        price = ctx.bar.close
        has_pos = ctx.has_position()

        ema30 = self._ema(closes, 30)
        rsi14 = self._rsi(closes, 14)

        if ema30 is None or rsi14 is None:
            return None

        # Exit logic: Sunday evening UTC (Sunday 18:00 - 23:00) or adverse momentum
        if has_pos and ctx.position_direction() == "long":
            is_sunday_exit = (weekday == 6 and hour >= 18)
            is_overextended = (rsi14 > 78.0)
            is_breakdown = (price < ema30 * 0.985)

            if is_sunday_exit or is_overextended or is_breakdown:
                reason = "weekend_close_target" if is_sunday_exit else ("rsi_overbought_exit" if is_overextended else "trend_breakdown")
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                        "ema30": ema30,
                        "rsi14": rsi14
                    }
                )
            return None

        # Entry logic: Friday late afternoon/evening (UTC 16:00 - 23:00)
        if not has_pos:
            if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
                return None

            # Track calendar week to avoid multiple entries in the same weekend
            current_week_id = current_dt.isocalendar()[1]
            if current_week_id == self.entry_week_id:
                return None

            is_friday_window = (weekday == 4 and 16 <= hour <= 23)
            if not is_friday_window:
                return None

            # Technical quality gate: price above or near EMA and RSI not overbought
            trend_support = price >= (ema30 * 0.995)
            momentum_ok = (40.0 <= rsi14 <= 66.0)

            # Avoid initiating new longs during severe crisis regimes
            market_regime = ctx.market.get("regime", "NORMAL")
            crisis_ok = market_regime not in ("CRISIS", "MELTDOWN")

            if trend_support and momentum_ok and crisis_ok:
                self.entry_week_id = current_week_id
                confidence = 0.70 if price > ema30 else 0.55
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=550.0,
                    take_profit_bps=950.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_seasonality_trend_aligned",
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                        "ema30": ema30,
                        "rsi14": rsi14,
                        "regime": market_regime
                    }
                )

        return None