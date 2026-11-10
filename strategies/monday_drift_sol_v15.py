from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.last_exit_bar = -999
        self.cooldown_bars = 12

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup + 1)
        if len(closes) < self.warmup:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        if ctx.has_position():
            # Exit seasonality trade by late Monday before Tuesday
            is_monday_late = (weekday == 0 and hour >= 20)
            is_tuesday = (weekday >= 1 and weekday <= 4)
            if is_monday_late or is_tuesday:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "monday_session_end", "weekday": weekday, "hour": hour}
                )

            # Volatility or severe reversal early emergency flat
            pos = ctx.position
            if pos and "entry_price" in pos and pos["entry_price"] > 0:
                entry = pos["entry_price"]
                cur = ctx.bar.close
                drawdown_bps = ((entry - cur) / entry) * 10000.0
                if drawdown_bps >= 300.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.9,
                        metadata={"reason": "early_risk_cut", "drawdown_bps": drawdown_bps}
                    )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry window: Sunday late UTC (18:00 - 23:00) into early Monday morning (00:00 - 04:00)
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 4)
        if not (is_sunday_entry or is_monday_early):
            return None

        rsi = self._rsi(closes, period=14)
        ema24 = self._ema(closes, period=24)
        if rsi is None or ema24 is None:
            return None

        # Avoid entering during excessive overbought blowout or collapse
        # Crypto weekend dip usually presents mild oversold / consolidating structure
        price = ctx.bar.close
        if rsi >= 62.0 or rsi <= 26.0:
            return None

        # Filter: Price holding near or above recent intraday EMA, rejecting marginal weak drifts
        ema_ratio = price / ema24
        if ema_ratio < 0.975:
            return None

        confidence = 0.65
        if rsi < 45.0:
            confidence += 0.15
        if price >= ema24:
            confidence += 0.10

        return ctx.signal(
            "long",
            confidence=min(confidence, 0.95),
            stop_loss_bps=350.0,
            take_profit_bps=550.0,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_into_monday_seasonality",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "ema24": round(ema24, 2),
                "price": round(price, 2),
                "regime": regime
            }
        )