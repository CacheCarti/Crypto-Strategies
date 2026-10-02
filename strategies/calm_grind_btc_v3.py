from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class CalmRegimeVolCarry(Strategy):
    METADATA = {
        "name": "Calm Regime Volatility Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_short_period = 40
        self.vol_baseline_period = 180
        self.ema_trend_period = 34
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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

    def _calc_vol(self, returns_slice: List[float]) -> float:
        n = len(returns_slice)
        if n < 2:
            return 0.0
        mean = sum(returns_slice) / n
        var = sum((x - mean) ** 2 for x in returns_slice) / (n - 1)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.vol_baseline_period + self.vol_short_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Calculate bar-to-bar returns
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]

        # Realized volatility over the short window
        current_vol = self._calc_vol(returns[-self.vol_short_period:])

        # Historical distribution of rolling volatility for baseline median
        vol_history = []
        step_needed = self.vol_baseline_period
        for i in range(step_needed):
            end_idx = len(returns) - i
            start_idx = end_idx - self.vol_short_period
            if start_idx >= 0:
                v = self._calc_vol(returns[start_idx:end_idx])
                vol_history.append(v)

        if not vol_history:
            return None

        sorted_vol = sorted(vol_history)
        median_vol = sorted_vol[len(sorted_vol) // 2]
        if median_vol <= 0:
            return None

        vol_ratio = current_vol / median_vol

        # Indicator values
        ema = self._ema(closes, self.ema_trend_period)
        rsi = self._rsi(closes, self.rsi_period)
        if ema is None or rsi is None:
            return None

        current_price = ctx.bar.close
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.regime

        has_pos = ctx.has_position()

        # Exit logic for open positions
        if has_pos:
            vol_spike = vol_ratio > 1.18
            bear_regime = trend_regime == "bear" or market_regime == "crisis" or crisis_score > 0.40
            trend_broken = current_price < (ema * 0.988)

            if vol_spike or bear_regime or trend_broken:
                reason = "vol_spike" if vol_spike else ("bear_crisis" if bear_regime else "trend_broken")
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": f"exit_{reason}",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_vol": round(current_vol, 5),
                        "median_vol": round(median_vol, 5),
                        "price": round(current_price, 2),
                        "ema": round(ema, 2),
                        "rsi": round(rsi, 2),
                        "trend_regime": trend_regime,
                        "crisis_score": round(crisis_score, 3),
                    },
                )
            return None

        # Entry logic (Long Only)
        in_cooldown = (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars
        if in_cooldown:
            return None

        is_quiet = vol_ratio < 0.92
        favorable_trend = trend_regime in ("bull", "neutral") and market_regime != "crisis" and crisis_score < 0.25
        price_support = current_price >= (ema * 0.995)
        rsi_healthy = 40.0 <= rsi <= 68.0

        if is_quiet and favorable_trend and price_support and rsi_healthy:
            # Scale confidence slightly higher in quiet bull trends
            conf = 0.70
            if trend_regime == "bull" and vol_ratio < 0.80:
                conf = 0.85

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_regime_carry_entry",
                    "vol_ratio": round(vol_ratio, 3),
                    "current_vol": round(current_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "price": round(current_price, 2),
                    "ema": round(ema, 2),
                    "rsi": round(rsi, 2),
                    "trend_regime": trend_regime,
                    "crisis_score": round(crisis_score, 3),
                },
            )

        return None