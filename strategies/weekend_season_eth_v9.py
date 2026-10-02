from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthSeasonalitySwing(Strategy):
    METADATA = {
        "name": "ETH Seasonality & Momentum Swing",
        "domain": "eth_usdc",
        "declared_sl_bps": 480.0,
        "declared_tp_bps": 920.0,
        "declared_hold_seconds": 172800,  # 48 hours target swing hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.entry_type = None

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        self.entry_type = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(80)
        if len(closes) < 60:
            return None

        current_price = ctx.bar.close
        timestamp = ctx.bar.timestamp
        weekday = timestamp.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = timestamp.hour

        rsi = self._rsi(closes, 14)
        ema30 = self._ema(closes, 30)
        ema60 = self._ema(closes, 60)

        if rsi is None or ema30 is None or ema60 is None:
            return None

        # Manage open position
        if ctx.has_position():
            # Exit 1: Calendar weekend cycle completion (Sunday late evening UTC or Monday dawn)
            if self.entry_type == "weekend_drift":
                if (weekday == 6 and hour >= 21) or (weekday == 0 and hour <= 4):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "weekend_drift_cycle_completed",
                            "weekday": weekday,
                            "hour": hour,
                            "rsi": round(rsi, 2),
                            "price": round(current_price, 2),
                        },
                    )

            # Exit 2: Technical exhaustion take-profit threshold
            if rsi > 76.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "rsi_overbought_exhaustion_exit",
                        "rsi": round(rsi, 2),
                        "price": round(current_price, 2),
                    },
                )
            return None

        # Enforce cooldown after prior trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # ENTRY SETUP 1: Weekend Seasonality Drift
        # Window: Friday 18:00 UTC through Saturday 04:00 UTC
        is_friday_eve = (weekday == 4 and hour >= 18)
        is_saturday_dawn = (weekday == 5 and hour <= 4)
        if (is_friday_eve or is_saturday_dawn) and (ctx.bar_index - self.last_entry_bar > 20):
            # Price filter: Avoid buying overbought blow-offs or severe breakdowns
            if 35.0 <= rsi <= 64.0 and current_price >= ema60 * 0.975:
                trend_bias = 0.80 if current_price >= ema30 else 0.65
                self.last_entry_bar = ctx.bar_index
                self.entry_type = "weekend_drift"
                return ctx.signal(
                    "long",
                    confidence=trend_bias,
                    stop_loss_bps=480.0,
                    take_profit_bps=920.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_weekend_drift_expansion",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema30": round(ema30, 2),
                        "price": round(current_price, 2),
                    },
                )

        # ENTRY SETUP 2: Midweek Trend Dip Mean Reversion
        # Window: Tuesday through Thursday with oversold pullback in general uptrend
        is_midweek = weekday in (1, 2, 3)
        if is_midweek and (ctx.bar_index - self.last_entry_bar > 12):
            if rsi <= 36.0 and current_price >= ema60 * 0.96:
                self.last_entry_bar = ctx.bar_index
                self.entry_type = "midweek_dip"
                return ctx.signal(
                    "long",
                    confidence=0.70,
                    stop_loss_bps=400.0,
                    take_profit_bps=800.0,
                    horizon_seconds=86400,
                    metadata={
                        "reason": "midweek_oversold_dip_pullback",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema60": round(ema60, 2),
                        "price": round(current_price, 2),
                    },
                )

        return None