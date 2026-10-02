from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetLead(Strategy):
    METADATA = {
        "name": "SolCrossAssetLead",
        "domain": "sol_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 20,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 8
        self.slow_period = 20
        self.btc_long_thresh = 1.2
        self.btc_short_thresh = -1.2
        self.btc_exit_thresh = 0.2
        self.cooldown_bars = 3
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)

        if ema_fast is None or ema_slow is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic for open positions
        if has_pos:
            if pos_dir == "long":
                if btc_ret < self.btc_exit_thresh or ema_fast < ema_slow:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "btc_momentum_fade_or_ema_bear_cross",
                            "btc_return_pct": btc_ret,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                        },
                    )
            elif pos_dir == "short":
                if btc_ret > -self.btc_exit_thresh or ema_fast > ema_slow:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "btc_momentum_fade_or_ema_bull_cross",
                            "btc_return_pct": btc_ret,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                        },
                    )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: BTC positive momentum confirmed by SOL EMA trend
        if btc_ret >= self.btc_long_thresh and ema_fast >= ema_slow:
            confidence = min(0.85, 0.55 + max(0.0, btc_ret) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_surge_sol_uptrend",
                    "btc_return_pct": btc_ret,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "close": closes[-1],
                },
            )

        # Short Entry: BTC negative momentum confirmed by SOL EMA trend
        if btc_ret <= self.btc_short_thresh and ema_fast <= ema_slow:
            confidence = min(0.85, 0.55 + abs(btc_ret) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_dump_sol_downtrend",
                    "btc_return_pct": btc_ret,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "close": closes[-1],
                },
            )

        return None