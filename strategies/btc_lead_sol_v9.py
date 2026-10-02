from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class BtcLeadSolMomentum(Strategy):
    METADATA = {
        "name": "BtcLeadSolMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 25,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 12
        self.btc_long_thresh = 1.6
        self.btc_short_thresh = -1.6
        self.btc_exit_thresh = 0.4
        self.cooldown_bars = 3
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
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
        closes = ctx.closes(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Feature read with fallback to market context
        btc_ret = ctx.features.get("btc_return_pct", ctx.market.get("btc_return_pct", 0.0))
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        current_close = ctx.bar.close

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()

            if direction == "long":
                if btc_ret < self.btc_exit_thresh or current_close < ema * 0.985:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "btc_momentum_flattened_or_sol_drop",
                            "btc_return_pct": btc_ret,
                            "sol_ema": ema,
                            "close": current_close,
                        },
                    )

            elif direction == "short":
                if btc_ret > -self.btc_exit_thresh or current_close > ema * 1.015:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "btc_momentum_flattened_or_sol_rebound",
                            "btc_return_pct": btc_ret,
                            "sol_ema": ema,
                            "close": current_close,
                        },
                    )

            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Entry: BTC showing positive return & SOL trading above short-term EMA
        if btc_ret >= self.btc_long_thresh and current_close >= ema:
            confidence = min(0.60 + (btc_ret - self.btc_long_thresh) * 0.05, 0.90)
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "btc_leader_positive_sol_above_ema",
                    "btc_return_pct": btc_ret,
                    "sol_ema": ema,
                    "close": current_close,
                },
            )

        # Short Entry: BTC showing negative return & SOL trading below short-term EMA
        if btc_ret <= self.btc_short_thresh and current_close <= ema:
            confidence = min(0.60 + abs(btc_ret - self.btc_short_thresh) * 0.05, 0.90)
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "btc_leader_negative_sol_below_ema",
                    "btc_return_pct": btc_ret,
                    "sol_ema": ema,
                    "close": current_close,
                },
            )

        return None