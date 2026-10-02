from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSolBetaMomentum(Strategy):
    METADATA = {
        "name": "BtcSolBetaMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.btc_long_thresh = 3.0
        self.btc_short_thresh = -3.0
        self.btc_exit_band = 1.5
        self.ema_period = 14
        self.roc_period = 6

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(30)
        if len(closes) < 20:
            return None

        btc_return = ctx.features.get("btc_return_pct", 0.0)
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None

        current_close = ctx.bar.close
        roc = ((current_close - closes[-self.roc_period]) / closes[-self.roc_period]) * 100.0

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if -self.btc_exit_band <= btc_return <= self.btc_exit_band:
                should_exit = True
                exit_reason = "btc_momentum_flattened"
            elif pos_dir == "long" and btc_return < -1.0:
                should_exit = True
                exit_reason = "btc_reversed_against_long"
            elif pos_dir == "short" and btc_return > 1.0:
                should_exit = True
                exit_reason = "btc_reversed_against_short"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "btc_return_pct": btc_return,
                        "sol_close": current_close,
                        "sol_roc": roc,
                        "ema14": ema_val,
                    },
                )
            return None

        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: BTC lead surge + SOL price above EMA + positive SOL momentum
        if btc_return >= self.btc_long_thresh and current_close > ema_val and roc > 0.25:
            conf = min(0.9, 0.6 + max(0.0, btc_return - self.btc_long_thresh) * 0.04)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_expansion_long",
                    "btc_return_pct": btc_return,
                    "sol_close": current_close,
                    "sol_roc": roc,
                    "ema14": ema_val,
                },
            )

        # Short Entry: BTC lead dump + SOL price below EMA + negative SOL momentum
        if btc_return <= self.btc_short_thresh and current_close < ema_val and roc < -0.25:
            conf = min(0.9, 0.6 + max(0.0, abs(btc_return) - abs(self.btc_short_thresh)) * 0.04)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_expansion_short",
                    "btc_return_pct": btc_return,
                    "sol_close": current_close,
                    "sol_roc": roc,
                    "ema14": ema_val,
                },
            )

        return None