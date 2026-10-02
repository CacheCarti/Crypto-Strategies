from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcLeadSolMomentum(Strategy):
    METADATA = {
        "name": "BtcLeadSolMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 720.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_long_thresh = 2.8
        self.btc_short_thresh = -2.8
        self.btc_exit_thresh = 1.2
        self.cooldown_bars = 5
        self.last_exit_bar = -999
        self.fast_ema_period = 10
        self.slow_ema_period = 24
        self.mom_period = 6

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
        closes = ctx.closes(self.slow_ema_period + 10)
        if len(closes) < self.slow_ema_period + 10:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)

        if ema_fast is None or ema_slow is None:
            return None

        current_close = closes[-1]
        past_close = closes[-1 - self.mom_period]
        sol_mom_pct = ((current_close - past_close) / past_close) * 100.0

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and btc_ret < self.btc_exit_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "btc_lead_momentum_flattened_long_exit",
                        "btc_return_pct": btc_ret,
                        "sol_close": current_close,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                    },
                )
            elif direction == "short" and btc_ret > -self.btc_exit_thresh:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "btc_lead_momentum_flattened_short_exit",
                        "btc_return_pct": btc_ret,
                        "sol_close": current_close,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: BTC strong positive 24h return + SOL local trend & momentum alignment
        if btc_ret >= self.btc_long_thresh and ema_fast > ema_slow and sol_mom_pct > 0.4:
            excess_btc = btc_ret - self.btc_long_thresh
            conf = min(0.95, max(0.55, 0.65 + excess_btc * 0.08))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "btc_surge_sol_trend_aligned_long",
                    "btc_return_pct": btc_ret,
                    "sol_mom_pct": sol_mom_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_close,
                },
            )

        # Short Entry: BTC strong negative 24h return + SOL local downtrend & momentum alignment
        if btc_ret <= self.btc_short_thresh and ema_fast < ema_slow and sol_mom_pct < -0.4:
            excess_btc = abs(btc_ret - self.btc_short_thresh)
            conf = min(0.95, max(0.55, 0.65 + excess_btc * 0.08))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "btc_dump_sol_trend_aligned_short",
                    "btc_return_pct": btc_ret,
                    "sol_mom_pct": sol_mom_pct,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_close,
                },
            )

        return None