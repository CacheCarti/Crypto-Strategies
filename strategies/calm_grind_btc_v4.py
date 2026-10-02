from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CalmRegimeCarryStrategy(Strategy):
    METADATA = {
        "name": "Calm Regime Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 200,
        "required_features": []
    }

    def initialize(self, ctx: BarContext) -> None:
        self.short_vol_period = 40
        self.long_vol_period = 180
        self.ema_fast = 24
        self.ema_slow = 60
        self.rsi_period = 14
        self.cooldown_bars = 14
        self.last_exit_bar = -999

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

    def _stddev_returns(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        rets = []
        for i in range(len(closes) - period, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                continue
            rets.append((closes[i] - prev) / prev)
        if len(rets) < period:
            return None
        mean = sum(rets) / len(rets)
        variance = sum((r - mean) ** 2 for r in rets) / len(rets)
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_vol_period + 5)
        if len(closes) < self.long_vol_period + 2:
            return None

        current_price = ctx.bar.close
        trend_regime = ctx.market.get("trend_regime", "neutral").lower()
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Skip during high market stress
        if ctx.regime in ["volatile", "crisis"] or crisis_score > 0.40:
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_crisis_regime", "crisis_score": crisis_score, "regime": ctx.regime}
                )
            return None

        short_vol = self._stddev_returns(closes, self.short_vol_period)
        long_vol = self._stddev_returns(closes, self.long_vol_period)
        ema_f = self._ema(closes, self.ema_fast)
        ema_s = self._ema(closes, self.ema_slow)
        rsi_val = self._rsi(closes, self.rsi_period)

        if short_vol is None or long_vol is None or ema_f is None or ema_s is None or rsi_val is None:
            return None

        has_pos = ctx.has_position()

        # Exit logic for open position (wider thresholds to prevent premature churn)
        if has_pos:
            vol_spike = short_vol > (long_vol * 1.30)
            is_bear = trend_regime == "bear"
            trend_broken = current_price < (ema_s * 0.975)
            rsi_blowoff = rsi_val > 82.0

            if vol_spike or is_bear or trend_broken or rsi_blowoff:
                self.last_exit_bar = ctx.bar_index
                reason = "vol_spike" if vol_spike else ("bear_regime" if is_bear else ("trend_broken" if trend_broken else "rsi_blowoff"))
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": f"exit_{reason}",
                        "short_vol": round(short_vol, 6),
                        "long_vol": round(long_vol, 6),
                        "rsi": round(rsi_val, 2),
                        "trend_regime": trend_regime,
                        "price": current_price
                    }
                )
            return None

        # Hard Cooldown Guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Tight Calm Carry Entry: Realized vol is strictly depressed and trend structure is positive
        is_calm = short_vol < (long_vol * 0.82)
        trend_aligned = trend_regime in ["bull", "neutral"] and ema_f >= ema_s
        price_above_slow_ema = current_price >= ema_s
        rsi_healthy = 46.0 <= rsi_val <= 64.0

        if is_calm and trend_aligned and price_above_slow_ema and rsi_healthy:
            vol_ratio = short_vol / max(long_vol, 1e-6)
            confidence = min(0.85, max(0.55, 0.95 - (vol_ratio * 0.40)))

            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_regime_carry_drift",
                    "short_vol": round(short_vol, 6),
                    "long_vol": round(long_vol, 6),
                    "vol_ratio": round(vol_ratio, 3),
                    "rsi": round(rsi_val, 2),
                    "ema_fast": round(ema_f, 2),
                    "ema_slow": round(ema_s, 2),
                    "trend_regime": trend_regime,
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index