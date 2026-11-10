from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across weekend
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.fast_ema_period = 12
        self.slow_ema_period = 36
        self.rsi_period = 14

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
        closes = ctx.closes(self.slow_ema_period + 5)
        if len(closes) < self.slow_ema_period:
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Exit logic: Sunday evening UTC (e.g., 20:00 - 23:00 UTC)
        if ctx.has_position():
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "sunday_scheduled_exit", "hour": hour, "weekday": weekday}
                )
            return None

        # Entry window: Friday between 18:00 and 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Avoid entries in crisis / high meltdown volatility states
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Trend & Momentum indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Material confirmation: Trend alignment and cross-asset confirmation
        # 1. Price should be above or close to fast EMA, and RSI not severely overbought (<68)
        # 2. Cross-asset check: BTC 24h return is not in severe breakdown (>-4.0%)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        price = ctx.bar.close
        trend_ok = (price >= fast_ema * 0.992) and (fast_ema >= slow_ema * 0.995)
        momentum_ok = (40.0 <= rsi <= 68.0)
        cross_asset_ok = (btc_ret > -4.0)

        if trend_ok and momentum_ok and cross_asset_ok:
            conf = 0.70
            if fast_ema > slow_ema:
                conf += 0.10
            if btc_ret > 0.5:
                conf += 0.10

            return ctx.signal(
                "long",
                confidence=min(conf, 0.95),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_seasonality_momentum_confirmed",
                    "rsi": round(rsi, 2),
                    "fast_ema": round(fast_ema, 2),
                    "slow_ema": round(slow_ema, 2),
                    "btc_ret": round(btc_ret, 2),
                    "hour": hour,
                    "weekday": weekday
                }
            )

        return None