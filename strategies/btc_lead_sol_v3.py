from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcSolCrossAssetMomentum(Strategy):
    METADATA = {
        "name": "BtcSolCrossAssetMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 25,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 15
        self.btc_long_threshold = 1.5
        self.btc_short_threshold = -1.5
        self.btc_long_exit = 0.2
        self.btc_short_exit = -0.2
        self.cooldown_bars = 2
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _get_normalized_btc_return(self, ctx: BarContext) -> float:
        val = ctx.features.get("btc_return_pct", None)
        if val is None:
            val = ctx.market.get("btc_return_pct", 0.0)
        try:
            val = float(val)
        except (ValueError, TypeError):
            val = 0.0
        # Normalize if passed as decimal (e.g. 0.025 instead of 2.5)
        if 0.0 < abs(val) < 0.25:
            val *= 100.0
        return val

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        btc_return = self._get_normalized_btc_return(ctx)
        in_position = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Manage open position exits
        if in_position:
            if pos_dir == "long":
                if btc_return <= self.btc_long_exit or current_price < ema * 0.992:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_btc_momentum_faded",
                            "btc_return_pct": btc_return,
                            "sol_price": current_price,
                            "sol_ema": ema,
                        },
                    )
            elif pos_dir == "short":
                if btc_return >= self.btc_short_exit or current_price > ema * 1.008:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_btc_momentum_faded",
                            "btc_return_pct": btc_return,
                            "sol_price": current_price,
                            "sol_ema": ema,
                        },
                    )
            return None

        # Cooldown guard after closing a position
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Bullish cross-asset setup: BTC trending up, SOL holding above short EMA
        if btc_return >= self.btc_long_threshold and current_price >= ema * 0.998:
            confidence = min(0.55 + (btc_return / 15.0), 0.90)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_momentum_sol_bullish_followthrough",
                    "btc_return_pct": btc_return,
                    "sol_price": current_price,
                    "sol_ema": ema,
                },
            )

        # Bearish cross-asset setup: BTC dumping, SOL trading below short EMA
        if btc_return <= self.btc_short_threshold and current_price <= ema * 1.002:
            confidence = min(0.55 + (abs(btc_return) / 15.0), 0.90)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_momentum_sol_bearish_followthrough",
                    "btc_return_pct": btc_return,
                    "sol_price": current_price,
                    "sol_ema": ema,
                },
            )

        return None