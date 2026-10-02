from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class CalmRegimeCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Carry BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 220,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_fast_period = 36
        self.vol_baseline_period = 160
        self.ema_trend_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _stddev(self, values: list) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return math.sqrt(var)

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        min_bars = self.vol_fast_period + self.vol_baseline_period + 5
        closes = ctx.closes(min_bars)
        if len(closes) < min_bars:
            return None

        # Calculate returns
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        if len(returns) < self.vol_fast_period + self.vol_baseline_period:
            return None

        # Compute rolling short-term realized volatility over recent baseline history
        rolling_vols = []
        total_eval_points = self.vol_baseline_period
        start_idx = len(returns) - total_eval_points

        for idx in range(start_idx, len(returns) + 1):
            window = returns[idx - self.vol_fast_period:idx]
            rolling_vols.append(self._stddev(window))

        if not rolling_vols:
            return None

        current_vol = rolling_vols[-1]
        sorted_baseline = sorted(rolling_vols[:-1])
        median_vol = sorted_baseline[len(sorted_baseline) // 2]
        if median_vol <= 1e-8:
            return None

        vol_ratio = current_vol / median_vol
        ema_val = self._ema(closes, self.ema_trend_period)
        rsi_val = self._rsi(closes, self.rsi_period)
        current_price = ctx.bar.close

        if ema_val is None or rsi_val is None:
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit conditions when holding Long
        if has_pos and pos_dir == "long":
            exit_reason = None
            if vol_ratio > 1.25:
                exit_reason = "volatility_spike_above_median"
            elif trend_regime == "bear":
                exit_reason = "regime_turned_bear"
            elif current_price < ema_val * 0.985:
                exit_reason = "price_cracked_trend_ema"
            elif rsi_val > 78.0:
                exit_reason = "rsi_overbought_exhaustion"

            if exit_reason:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "trend_regime": trend_regime,
                        "rsi": round(rsi_val, 2),
                        "price": round(current_price, 2),
                        "ema50": round(ema_val, 2),
                    },
                )
            return None

        # Entry conditions for new Long position
        if not has_pos:
            bars_since_exit = ctx.bar_index - self.last_exit_bar
            if bars_since_exit < self.cooldown_bars:
                return None

            is_calm_tape = vol_ratio < 0.92
            is_valid_trend = trend_regime in ("bull", "neutral")
            is_above_trend = current_price >= ema_val * 0.998
            is_healthy_rsi = 40.0 <= rsi_val <= 66.0

            if is_calm_tape and is_valid_trend and is_above_trend and is_healthy_rsi:
                confidence = 0.65
                if trend_regime == "bull":
                    confidence += 0.15
                if vol_ratio < 0.75:
                    confidence += 0.10
                confidence = min(0.95, confidence)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "calm_regime_carry_quiet_grind",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "trend_regime": trend_regime,
                        "rsi": round(rsi_val, 2),
                        "price": round(current_price, 2),
                        "ema50": round(ema_val, 2),
                        "confidence": round(confidence, 2),
                    },
                )

        return None