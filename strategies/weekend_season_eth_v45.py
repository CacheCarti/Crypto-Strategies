from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_bars = 50
        self.rsi_period = 14
        self.trend_period = 48
        self.cooldown_bars = 24
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Manage existing position
        if ctx.has_position():
            # Check exit conditions: Late Sunday (>= 20 UTC) or early Monday
            is_exit_time = (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 4)
            bars_in_trade = ctx.bar_index - self.entry_bar

            # Cut trade if exit window reached or held for more than ~50 hours
            if is_exit_time or bars_in_trade >= 52:
                rsi_val = self._rsi(closes, self.rsi_period) or 50.0
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "weekend_cycle_exit",
                        "bars_held": bars_in_trade,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "price": current_price,
                    },
                )
            return None

        # Cooldown guard: must wait after last exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Friday 18:00 - 23:00 UTC
        is_friday_entry_window = (weekday == 4) and (18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Indicators for high-conviction filtration
        trend_sma = self._sma(closes, self.trend_period)
        rsi = self._rsi(closes, self.rsi_period)
        if trend_sma is None or rsi is None:
            return None

        # Trend & Momentum filter:
        # Require price above 48h trend and RSI not severely overbought (>65)
        # Also ensure market is not in an outright meltdown or crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        if crisis_score > 0.65:
            return None

        if current_price < trend_sma:
            return None

        if rsi > 65.0 or rsi < 32.0:
            return None

        # Confidence scaling based on alignment of trend and fear-greed sentiment
        conf = 0.65
        if fg_index > 40.0:
            conf += 0.10
        if current_price > trend_sma * 1.005:
            conf += 0.10
        conf = min(conf, 0.95)

        self.entry_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "friday_evening_weekend_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "trend_sma48": round(trend_sma, 2),
                "fear_greed": fg_index,
                "crisis_score": crisis_score,
                "price": current_price,
            },
        )