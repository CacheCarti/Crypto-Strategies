from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class CalmTrendMomentum(Strategy):
    METADATA = {
        "name": "CalmTrendMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_short_window = 24
        self.vol_baseline_window = 180
        self.ema_fast = 20
        self.ema_slow = 50
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_exit_bar = -999

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        if len(gains) < period:
            return None
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _stddev(self, data):
        n = len(data)
        if n < 2:
            return 0.0
        mean = sum(data) / n
        var = sum((x - mean) ** 2 for x in data) / (n - 1)
        return math.sqrt(var)

    def _get_vol_metrics(self, closes):
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        req_len = self.vol_short_window + self.vol_baseline_window
        if len(rets) < req_len:
            return None, None

        current_vol = self._stddev(rets[-self.vol_short_window:])
        hist_vols = []
        for i in range(len(rets) - self.vol_baseline_window, len(rets) - self.vol_short_window + 1):
            window_rets = rets[i:i + self.vol_short_window]
            hist_vols.append(self._stddev(window_rets))

        if not hist_vols:
            return current_vol, current_vol

        hist_vols.sort()
        median_vol = hist_vols[len(hist_vols) // 2]
        return current_vol, median_vol

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter severe market stress
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.60:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score, "regime": regime},
                )
            return None

        current_vol, median_vol = self._get_vol_metrics(closes)
        if current_vol is None or median_vol is None or median_vol <= 0:
            return None

        ema_fast_curr = self._ema(closes, self.ema_fast)
        ema_fast_prev = self._ema(closes[:-1], self.ema_fast)
        ema_slow_curr = self._ema(closes, self.ema_slow)
        rsi = self._rsi(closes, self.rsi_period)

        if None in (ema_fast_curr, ema_fast_prev, ema_slow_curr, rsi):
            return None

        current_price = closes[-1]
        prev_price = closes[-2]
        vol_ratio = current_vol / median_vol
        is_calm = vol_ratio < 0.82
        is_vol_spike = vol_ratio > 1.40

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()

            if pos_dir == "long":
                # Exit on volatility explosion, deep trend breakdown, or RSI exhaustion
                if is_vol_spike or (current_price < ema_slow_curr and prev_price < ema_slow_curr) or rsi > 78.0:
                    reason = "vol_spike" if is_vol_spike else ("rsi_exhaustion" if rsi > 78.0 else "trend_breakdown")
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": f"long_exit_{reason}",
                            "price": current_price,
                            "vol_ratio": round(vol_ratio, 3),
                            "rsi": round(rsi, 2),
                        },
                    )
            elif pos_dir == "short":
                # Exit on volatility explosion, deep trend breakout, or RSI exhaustion
                if is_vol_spike or (current_price > ema_slow_curr and prev_price > ema_slow_curr) or rsi < 22.0:
                    reason = "vol_spike" if is_vol_spike else ("rsi_exhaustion" if rsi < 22.0 else "trend_breakout")
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": f"short_exit_{reason}",
                            "price": current_price,
                            "vol_ratio": round(vol_ratio, 3),
                            "rsi": round(rsi, 2),
                        },
                    )
            return None

        # Hard multi-bar cooldown after exit to eliminate churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry strictly gated on calm volatility regime
        if not is_calm:
            return None

        ema_slope_up = ema_fast_curr > ema_fast_prev
        ema_slope_down = ema_fast_curr < ema_fast_prev
        trend_bull = current_price > ema_fast_curr and ema_fast_curr > ema_slow_curr
        trend_bear = current_price < ema_fast_curr and ema_fast_curr < ema_slow_curr

        # High-conviction Long trigger: fresh breakout / re-acceleration above fast EMA in established calm uptrend
        long_trigger = (prev_price <= ema_fast_prev and current_price > ema_fast_curr) or (
            current_price > max(closes[-6:-1]) and 53.0 <= rsi <= 65.0
        )

        if trend_bull and ema_slope_up and long_trigger and 52.0 <= rsi <= 66.0:
            confidence = min(0.90, max(0.55, 0.55 + (0.82 - vol_ratio) * 0.4))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_trend_bull_breakout",
                    "price": current_price,
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "vol_ratio": round(vol_ratio, 3),
                    "rsi": round(rsi, 2),
                },
            )

        # High-conviction Short trigger: fresh breakdown / re-acceleration below fast EMA in established calm downtrend
        short_trigger = (prev_price >= ema_fast_prev and current_price < ema_fast_curr) or (
            current_price < min(closes[-6:-1]) and 35.0 <= rsi <= 47.0
        )

        if trend_bear and ema_slope_down and short_trigger and 34.0 <= rsi <= 48.0:
            confidence = min(0.90, max(0.55, 0.55 + (0.82 - vol_ratio) * 0.4))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_trend_bear_breakdown",
                    "price": current_price,
                    "ema_fast": round(ema_fast_curr, 2),
                    "ema_slow": round(ema_slow_curr, 2),
                    "vol_ratio": round(vol_ratio, 3),
                    "rsi": round(rsi, 2),
                },
            )

        return None