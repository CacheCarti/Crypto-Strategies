from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class LowVolTrendBTC(Strategy):
    METADATA = {
        "name": "LowVolTrendBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 20
        self.ema_slow_period = 50
        self.vol_window = 24
        self.history_window = 180
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _calc_vol_and_median(self, closes: list) -> tuple[Optional[float], Optional[float]]:
        min_len = self.vol_window + self.history_window + 1
        if len(closes) < min_len:
            return None, None
        
        rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
        vols = []
        for j in range(len(rets) - self.vol_window + 1):
            window = rets[j:j + self.vol_window]
            mean_ret = sum(window) / self.vol_window
            variance = sum((r - mean_ret) ** 2 for r in window) / self.vol_window
            vols.append(math.sqrt(variance))
        
        if not vols:
            return None, None
            
        current_vol = vols[-1]
        eval_window = vols[-self.history_window:]
        sorted_vols = sorted(eval_window)
        median_vol = sorted_vols[len(sorted_vols) // 2]
        return current_vol, median_vol

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        # Market regime filter: avoid crisis tape
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40 or ctx.regime == "crisis":
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": round(crisis_score, 3), "regime": regime}
                )
            return None

        closes = ctx.closes(self.vol_window + self.history_window + 10)
        if len(closes) < self.vol_window + self.history_window + 5:
            return None

        current_vol, median_vol = self._calc_vol_and_median(closes)
        if current_vol is None or median_vol is None or median_vol == 0:
            return None

        ema20 = self._ema(closes, self.ema_fast_period)
        ema20_prev4 = self._ema(closes[:-4], self.ema_fast_period)
        ema50 = self._ema(closes, self.ema_slow_period)

        if ema20 is None or ema20_prev4 is None or ema50 is None or ema20_prev4 == 0:
            return None

        price = ctx.bar.close
        ema_slope = (ema20 - ema20_prev4) / ema20_prev4
        slope_bps = ema_slope * 10000.0
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Position exit management: allow trades to run, only exit on severe shock or full trend breakdown
        if has_pos:
            # Stand down only on extreme volatility blowup
            if current_vol > median_vol * 1.85:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "severe_vol_spike_exit",
                        "current_vol": round(current_vol, 6),
                        "median_vol": round(median_vol, 6),
                        "price": price,
                    }
                )

            # Exit long on confirmed trend reversal (price well below EMA50 and EMA slope negative)
            if pos_dir == "long" and price < ema50 and slope_bps < -3.0:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "long_trend_invalidation",
                        "price": price,
                        "ema50": round(ema50, 2),
                        "slope_bps": round(slope_bps, 2),
                    }
                )

            # Exit short on confirmed trend reversal (price well above EMA50 and EMA slope positive)
            if pos_dir == "short" and price > ema50 and slope_bps > 3.0:
                return ctx.signal(
                    "flat",
                    confidence=0.70,
                    metadata={
                        "reason": "short_trend_invalidation",
                        "price": price,
                        "ema50": round(ema50, 2),
                        "slope_bps": round(slope_bps, 2),
                    }
                )

            return None

        # Hard cooldown after exit and entry to prevent churning
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Strict low-volatility compression filter: vol must be distinctly below median
        if current_vol > median_vol * 0.85:
            return None

        vol_ratio = current_vol / median_vol
        base_conf = 0.75 + min(0.15, max(0.0, (0.85 - vol_ratio) * 0.4))

        # Long Entry: Clear alignment in calm tape (Price > EMA20 > EMA50, strong upward slope, price clearance)
        if (price > ema20 * 1.002) and (ema20 > ema50) and (slope_bps > 6.0) and (closes[-1] > closes[-3]):
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(base_conf, 0.90),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_tape_bullish_breakout",
                    "price": price,
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "slope_bps": round(slope_bps, 2),
                    "vol_ratio": round(vol_ratio, 3),
                }
            )

        # Short Entry: Clear alignment in calm tape (Price < EMA20 < EMA50, strong downward slope, price clearance)
        if (price < ema20 * 0.998) and (ema20 < ema50) and (slope_bps < -6.0) and (closes[-1] < closes[-3]):
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(base_conf, 0.90),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "calm_tape_bearish_breakdown",
                    "price": price,
                    "ema20": round(ema20, 2),
                    "ema50": round(ema50, 2),
                    "slope_bps": round(slope_bps, 2),
                    "vol_ratio": round(vol_ratio, 3),
                }
            )

        return None