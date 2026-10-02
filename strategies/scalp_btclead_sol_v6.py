import math
from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal


class CrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "CrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 175.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 4
        self.cooldown_bars = 16
        self.max_hold_bars = 5
        self.min_btc_impulse = 0.75
        self.max_sol_response_ratio = 0.45
        self.btc_hist = []
        self.entry_bar = -1
        self.last_exit_bar = -100

    def _rsi(self, closes, period=7):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

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
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_hist.append(btc_ret)
        if len(self.btc_hist) > 20:
            self.btc_hist.pop(0)

        if len(self.btc_hist) <= self.lookback:
            return None

        current_price = ctx.bar.close
        ref_price = closes[-1 - self.lookback]
        sol_ret_pct = ((current_price - ref_price) / ref_price) * 100.0
        btc_impulse = btc_ret - self.btc_hist[-1 - self.lookback]

        rsi = self._rsi(closes, period=7)
        ema_fast = self._ema(closes, 9)

        # In-position time-based exit guard
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_exit",
                        "bars_held": bars_held,
                        "sol_ret": round(sol_ret_pct, 3),
                        "btc_impulse": round(btc_impulse, 3),
                    },
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Cross-asset impulse and lag conditions
        bullish_lag = (
            btc_impulse >= self.min_btc_impulse
            and sol_ret_pct < (btc_impulse * self.max_sol_response_ratio)
            and (rsi is not None and rsi < 68.0)
            and (ema_fast is not None and current_price >= ema_fast * 0.998)
        )

        bearish_lag = (
            btc_impulse <= -self.min_btc_impulse
            and sol_ret_pct > (btc_impulse * self.max_sol_response_ratio)
            and (rsi is not None and rsi > 32.0)
            and (ema_fast is not None and current_price <= ema_fast * 1.002)
        )

        if bullish_lag:
            lag_spread = btc_impulse - sol_ret_pct
            conf = min(0.9, max(0.55, 0.55 + lag_spread * 0.15))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_bullish",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret": round(sol_ret_pct, 3),
                    "lag_spread": round(lag_spread, 3),
                    "rsi": round(rsi, 2) if rsi else 50.0,
                    "price": current_price,
                },
            )

        if bearish_lag:
            lag_spread = sol_ret_pct - btc_impulse
            conf = min(0.9, max(0.55, 0.55 + lag_spread * 0.15))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_bearish",
                    "btc_impulse": round(btc_impulse, 3),
                    "sol_ret": round(sol_ret_pct, 3),
                    "lag_spread": round(lag_spread, 3),
                    "rsi": round(rsi, 2) if rsi else 50.0,
                    "price": current_price,
                },
            )

        return None