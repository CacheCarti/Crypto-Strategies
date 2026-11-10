from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar_idx = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        bar_time = ctx.bar.timestamp
        weekday = bar_time.weekday()  # Monday is 0, Sunday is 6
        hour = bar_time.hour
        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            # Seasonality exit: Tuesday 00:00 UTC or later, or Monday evening after 22:00 UTC
            time_to_exit = (weekday == 0 and hour >= 22) or (weekday == 1 and hour >= 2)
            
            # Stop early if trend completely breaks below EMA50
            ema50 = self._ema(closes, 50)
            technical_exit = (ema50 is not None) and (price < ema50 * 0.975)

            if time_to_exit or technical_exit:
                self.last_exit_bar = ctx.bar_index
                reason = "monday_session_end" if time_to_exit else "technical_stop_ema50"
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": reason, "exit_price": price, "weekday": weekday, "hour": hour}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Crisis / Regime check
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry window: Sunday 18:00 UTC through Monday 06:00 UTC
        in_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 6)
        if not in_entry_window:
            return None

        # Momentum & oversold confirmation
        rsi14 = self._rsi(closes, 14)
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)

        if rsi14 is None or ema20 is None or ema50 is None:
            return None

        # Avoid chasing if already excessively overbought heading into the week
        # Enter when RSI is reasonably contained (< 65) and price is above short-term momentum or within 2% of EMA20
        momentum_ok = price >= (ema20 * 0.98)
        not_overbought = rsi14 < 65.0

        # Sentiment check (avoid extreme fear market capitulation)
        fg_index = float(ctx.features.get("fear_greed_index", 50))
        sentiment_ok = fg_index >= 20.0

        if momentum_ok and not_overbought and sentiment_ok:
            confidence = 0.65
            if price > ema20 and ema20 > ema50:
                confidence = 0.85
            
            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_monday_drift_entry",
                    "rsi14": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "price": price,
                    "fear_greed": fg_index,
                    "weekday": weekday,
                    "hour": hour
                }
            )

        return None