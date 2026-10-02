from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any


class SolCrossAssetBtcLead(Strategy):
    METADATA = {
        "name": "SolCrossAssetBtcLead",
        "domain": "sol_usdc",
        "declared_sl_bps": 300.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 25,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 20
        self.btc_long_thresh = 1.8
        self.btc_short_thresh = -1.8
        self.btc_exit_flat_thresh = 0.6
        self.cooldown_bars = 3
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.trend_period + 5)
        if len(closes) < self.trend_period:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        ema = self._ema(closes, self.trend_period)
        if ema is None:
            return None

        curr_price = ctx.bar.close
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle active position exits when BTC lead flattens or price crosses EMA
        if has_pos:
            if pos_dir == "long":
                if btc_ret < self.btc_exit_flat_thresh or curr_price < ema * 0.99:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "exit_long_btc_flattened_or_trend_lost",
                            "btc_return_pct": round(btc_ret, 3),
                            "ema": round(ema, 3),
                            "price": curr_price,
                        },
                    )
            elif pos_dir == "short":
                if btc_ret > -self.btc_exit_flat_thresh or curr_price > ema * 1.01:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "exit_short_btc_flattened_or_trend_lost",
                            "btc_return_pct": round(btc_ret, 3),
                            "ema": round(ema, 3),
                            "price": curr_price,
                        },
                    )
            return None

        # Mandatory cooldown check after last trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Loosened Entry Logic: BTC directional lead + SOL tape confirmation
        if btc_ret >= self.btc_long_thresh and curr_price >= ema:
            confidence = min(0.9, 0.6 + (btc_ret - self.btc_long_thresh) * 0.05)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "btc_lead_bull_sol_above_ema",
                    "btc_return_pct": round(btc_ret, 3),
                    "ema": round(ema, 3),
                    "price": curr_price,
                },
            )

        if btc_ret <= self.btc_short_thresh and curr_price <= ema:
            confidence = min(0.9, 0.6 + abs(btc_ret - self.btc_short_thresh) * 0.05)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=300.0,
                take_profit_bps=600.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "btc_lead_bear_sol_below_ema",
                    "btc_return_pct": round(btc_ret, 3),
                    "ema": round(ema, 3),
                    "price": curr_price,
                },
            )

        return None