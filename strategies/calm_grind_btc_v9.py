from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CalmRegimeCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.short_vol_period = 36
        self.long_vol_period = 180
        self.ema_fast_period = 21
        self.ema_slow_period = 55
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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

    def _realized_vol(self, closes, period: int):
        if len(closes) < period + 1:
            return None
        rets = []
        for i in range(len(closes) - period, len(closes)):
            if closes[i - 1] > 0:
                rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
        if len(rets) < 2:
            return None
        mean_ret = sum(rets) / len(rets)
        var = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.long_vol_period + self.short_vol_period + 5
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # 1. Volatility calculation
        current_short_vol = self._realized_vol(closes, self.short_vol_period)
        if current_short_vol is None:
            return None

        # Sample historical short vols to calculate median baseline
        hist_short_vols = []
        step = 4
        for offset in range(0, self.long_vol_period, step):
            end_idx = len(closes) - offset
            start_idx = end_idx - self.short_vol_period - 1
            if start_idx >= 0:
                sub_closes = closes[start_idx:end_idx]
                sv = self._realized_vol(sub_closes, self.short_vol_period)
                if sv is not None:
                    hist_short_vols.append(sv)

        if not hist_short_vols:
            return None

        hist_short_vols.sort()
        vol_median = hist_short_vols[len(hist_short_vols) // 2]
        is_quiet = current_short_vol < vol_median and crisis_score < 0.35

        # 2. Indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        has_pos = ctx.has_position()
        curr_price = ctx.bar.close

        # Manage open position
        if has_pos:
            should_exit = False
            exit_reason = ""

            if trend_regime == "bear":
                should_exit = True
                exit_reason = "regime_turned_bear"
            elif current_short_vol > vol_median * 1.35:
                should_exit = True
                exit_reason = "volatility_spike_exit"
            elif crisis_score >= 0.50:
                should_exit = True
                exit_reason = "crisis_score_threshold_hit"
            elif curr_price < ema_slow * 0.985:
                should_exit = True
                exit_reason = "trend_structural_breakdown"
            elif rsi > 78.0:
                should_exit = True
                exit_reason = "overbought_mean_reversion_exit"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "short_vol": round(current_short_vol, 6),
                        "vol_median": round(vol_median, 6),
                        "rsi": round(rsi, 2),
                        "crisis_score": round(crisis_score, 3),
                        "trend_regime": trend_regime,
                    },
                )
            return None

        # Entry logic (longs only during calm carry regimes)
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        regime_ok = trend_regime in ("bull", "neutral")
        trend_ok = curr_price >= ema_fast and ema_fast >= ema_slow * 0.995
        momentum_ok = 45.0 <= rsi <= 68.0

        if is_quiet and regime_ok and trend_ok and momentum_ok:
            confidence = 0.65
            if trend_regime == "bull":
                confidence += 0.15
            if current_short_vol < vol_median * 0.8:
                confidence += 0.10
            confidence = min(0.95, confidence)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "calm_regime_carry_expansion",
                    "short_vol": round(current_short_vol, 6),
                    "vol_median": round(vol_median, 6),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "trend_regime": trend_regime,
                    "crisis_score": round(crisis_score, 3),
                },
            )

        return None