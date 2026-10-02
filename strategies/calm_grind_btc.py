from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class CalmRegimeCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 48
        self.baseline_window = 160
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_exit_bar = -100

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

    def _calc_vol(self, closes_slice) -> float:
        n = len(closes_slice)
        if n < 2:
            return 0.0
        rets = [(closes_slice[i] - closes_slice[i - 1]) / closes_slice[i - 1] for i in range(1, n)]
        mean_ret = sum(rets) / len(rets)
        var = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
        return math.sqrt(var)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_window + self.vol_window + 10)
        if len(closes) < self.baseline_window + self.vol_window:
            return None

        current_vol = self._calc_vol(closes[-self.vol_window:])

        vol_samples = []
        for offset in range(0, self.baseline_window, 8):
            end_idx = len(closes) - offset
            start_idx = end_idx - self.vol_window
            if start_idx < 1:
                break
            sample_vol = self._calc_vol(closes[start_idx - 1:end_idx])
            vol_samples.append(sample_vol)

        if not vol_samples:
            return None

        vol_samples.sort()
        median_vol = vol_samples[len(vol_samples) // 2]
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol
        ema20 = self._ema(closes, self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        price = ctx.bar.close
        trend_regime = ctx.market.get("trend_regime", "neutral")
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Position Management
        if ctx.has_position():
            vol_spike = vol_ratio > 1.25
            is_crisis = market_regime in ("CRISIS", "MELTDOWN") or crisis_score >= 0.50
            trend_fail = trend_regime == "bear" and price < ema50

            if vol_spike or is_crisis or trend_fail:
                self.last_exit_bar = ctx.bar_index
                reason = "vol_spike" if vol_spike else ("crisis_regime" if is_crisis else "trend_failure")
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": f"exit_{reason}",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "price": price,
                        "ema50": round(ema50, 2),
                        "trend_regime": trend_regime,
                    },
                )
            return None

        # Cooldown guard: prevent overtrading and friction burn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Tight Entry Filters: strict quiet tape + confirmed bullish trend structure
        is_quiet_tape = vol_ratio < 0.88 and market_regime == "NORMAL" and crisis_score < 0.25
        trend_aligned = trend_regime in ("bull", "neutral") and ema20 > ema50 and price >= ema20
        momentum_steady = 45.0 <= rsi <= 64.0

        if is_quiet_tape and trend_aligned and momentum_steady:
            confidence = 0.85 if trend_regime == "bull" else 0.70
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=900.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "calm_regime_carry_entry",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "price": price,
                    "trend_regime": trend_regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index