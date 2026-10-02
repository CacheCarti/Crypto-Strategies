from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class CalmRegimeCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 720.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_window = 36
        self.baseline_window = 180
        self.cooldown_bars = 6
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

    def _realized_vol(self, closes: list, window: int) -> Optional[float]:
        if len(closes) < window + 1:
            return None
        returns = []
        for i in range(len(closes) - window, len(closes)):
            prev = closes[i - 1]
            if prev <= 0:
                return None
            returns.append((closes[i] - prev) / prev)
        mean_ret = sum(returns) / window
        var = sum((r - mean_ret) ** 2 for r in returns) / window
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.baseline_window + self.vol_window + 5
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        current_vol = self._realized_vol(closes, self.vol_window)
        if current_vol is None or current_vol == 0:
            return None

        # Sample historical rolling volatilities to compute median baseline
        hist_vols = []
        step = 4
        start_idx = len(closes) - self.baseline_window
        for i in range(start_idx, len(closes) + 1, step):
            sub_closes = closes[:i]
            v = self._realized_vol(sub_closes, self.vol_window)
            if v is not None:
                hist_vols.append(v)

        if len(hist_vols) < 10:
            return None

        hist_vols.sort()
        median_vol = hist_vols[len(hist_vols) // 2]
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        ema_trend = self._ema(closes, 48)
        rsi = self._rsi(closes, 14)
        if ema_trend is None or rsi is None:
            return None

        price = ctx.bar.close
        is_quiet = vol_ratio < 0.95
        is_trend_safe = trend_regime in ("bull", "neutral") and price > (ema_trend * 0.992)

        # Handle active position management
        if ctx.has_position():
            vol_spike = vol_ratio > 1.25
            regime_broken = trend_regime == "bear" or crisis_score > 0.45
            price_dump = price < (ema_trend * 0.975)

            if vol_spike or regime_broken or price_dump:
                self.last_exit_bar = ctx.bar_index
                reason = "volatility_spike_exit" if vol_spike else ("regime_bear_exit" if regime_broken else "trend_break_exit")
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "vol_ratio": round(vol_ratio, 3),
                        "trend_regime": trend_regime,
                        "rsi": round(rsi, 2),
                        "price": round(price, 2),
                    },
                )
            return None

        # Entry logic: quiet tape + non-bear trend + cooldown passed
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            return None

        if is_quiet and is_trend_safe and 38.0 <= rsi <= 68.0 and crisis_score < 0.30:
            confidence = min(0.85, max(0.55, 0.90 - 0.35 * vol_ratio))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_regime_carry_entry",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 6),
                    "median_vol": round(median_vol, 6),
                    "trend_regime": trend_regime,
                    "rsi": round(rsi, 2),
                    "ema48": round(ema_trend, 2),
                    "price": round(price, 2),
                },
            )

        return None