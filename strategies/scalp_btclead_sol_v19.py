from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaCatchup(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaCatchup",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,  # ~5 bars of 5m
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.btc_threshold = 1.6
        self.sol_lag_max = 0.50

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        # Manage existing open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_timeout",
                    "bars_held": bars_held,
                    "price": ctx.bar.close
                })
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Avoid crisis/illiquid spikes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # BTC 24h return feature
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        btc_mag = abs(btc_ret)

        if btc_mag < self.btc_threshold:
            return None

        # SOL local 3-bar return percentage
        sol_ret_3bar = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # ATR filter for minimal tradeable volatility
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_pct = (atr / closes[-1]) * 100.0
        if atr_pct < 0.20:
            return None

        # Long Catch-up: BTC surged > 1.6%, SOL has not moved up significantly yet (< 0.5%)
        if btc_ret >= self.btc_threshold and sol_ret_3bar < self.sol_lag_max and sol_ret_3bar > -1.5:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.90, max(0.60, 0.50 + (btc_ret / 10.0)))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3bar": sol_ret_3bar,
                    "atr_pct": atr_pct,
                    "price": ctx.bar.close
                }
            )

        # Short Catch-up: BTC dumped < -1.6%, SOL has not dropped significantly yet (> -0.5%)
        if btc_ret <= -self.btc_threshold and sol_ret_3bar > -self.sol_lag_max and sol_ret_3bar < 1.5:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.90, max(0.60, 0.50 + (abs(btc_ret) / 10.0)))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=120.0,
                take_profit_bps=220.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_ret,
                    "sol_ret_3bar": sol_ret_3bar,
                    "atr_pct": atr_pct,
                    "price": ctx.bar.close
                }
            )

        return None