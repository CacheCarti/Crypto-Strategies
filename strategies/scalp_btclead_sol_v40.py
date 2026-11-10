from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.min_bars_held = 3
        self.max_bars_held = 6
        self.btc_window = 24

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 30:
            return None

        # Check existing position time-based exit (holding 3 to 6 bars)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_bars_held:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_horizon_reached", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown guard
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Feature reading: BTC 24h return percentage
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if abs(btc_ret) < 1.0:
            return None

        # SOL short-term return (last 3 bars = 15 mins) and medium trend (12 bars = 1 hr)
        sol_ret_15m = (closes[-1] - closes[-4]) / closes[-4] * 100.0
        sol_ret_1h = (closes[-1] - closes[-13]) / closes[-13] * 100.0

        # SOL local volatility via normalized ATR proxy
        highs = ctx.highs(15)
        lows = ctx.lows(15)
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, 15)]
        atr = sum(trs) / len(trs)
        atr_pct = (atr / closes[-1]) * 100.0

        # Dynamically scale thresholds based on local volatility
        lag_threshold = max(0.20, atr_pct * 0.7)

        # BTC bullish impulse with SOL lag
        if btc_ret > 1.3:
            # SOL hasn't moved yet or has just begun moving relative to BTC's strong move
            if sol_ret_15m < 0.15 and sol_ret_1h < (btc_ret * 0.45):
                divergence = btc_ret - sol_ret_15m
                if divergence > lag_threshold:
                    self.entry_bar = ctx.bar_index
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "btc_lead_sol_lag_long",
                            "btc_ret": round(btc_ret, 3),
                            "sol_ret_15m": round(sol_ret_15m, 3),
                            "sol_ret_1h": round(sol_ret_1h, 3),
                            "divergence": round(divergence, 3)
                        }
                    )

        # BTC bearish impulse with SOL lag
        elif btc_ret < -1.3:
            # SOL hasn't dropped sharply yet
            if sol_ret_15m > -0.15 and sol_ret_1h > (btc_ret * 0.45):
                divergence = sol_ret_15m - btc_ret
                if divergence > lag_threshold:
                    self.entry_bar = ctx.bar_index
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "btc_lead_sol_lag_short",
                            "btc_ret": round(btc_ret, 3),
                            "sol_ret_15m": round(sol_ret_15m, 3),
                            "sol_ret_1h": round(sol_ret_1h, 3),
                            "divergence": round(divergence, 3)
                        }
                    )

        return None