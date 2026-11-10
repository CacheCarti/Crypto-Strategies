from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.max_hold_bars = 4
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.entry_dir = None
        self.btc_window = 3

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100
        self.entry_dir = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position time-stop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_catchup_elapsed", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid crisis/meltdown regimes where correlations break down unpredictably
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        
        # Freshness calculation: lookback return change
        # Approximate freshness by measuring recent SOL return over the same short window
        sol_window = 3
        sol_ret = (closes[-1] - closes[-1 - sol_window]) / closes[-1 - sol_window] * 100.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or closes[-1] == 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # Filter for rare, high-conviction momentum divergence:
        # 1. BTC moved sharply (> 1.6% 24h return absolute)
        # 2. SOL has lagged significantly (has not covered even half of BTC's recent delta)
        # 3. Microstructure confirmation: SOL short-term candle shows first sign of reacting
        btc_abs = abs(current_btc_ret)
        if btc_abs < 1.6:
            return None

        long_trigger = (current_btc_ret >= 1.6) and (sol_ret < 0.35) and (closes[-1] > closes[-2])
        short_trigger = (current_btc_ret <= -1.6) and (sol_ret > -0.35) and (closes[-1] < closes[-2])

        if long_trigger:
            self.entry_bar = ctx.bar_index
            self.entry_dir = "long"
            conf = min(0.9, max(0.6, 0.5 + (btc_abs - 1.6) * 0.15))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_lag_bullish_catchup",
                    "btc_ret": round(current_btc_ret, 3),
                    "sol_ret_3bar": round(sol_ret, 3),
                    "atr_pct": round(atr_pct, 3),
                    "close": closes[-1]
                }
            )

        if short_trigger:
            self.entry_bar = ctx.bar_index
            self.entry_dir = "short"
            conf = min(0.9, max(0.6, 0.5 + (btc_abs - 1.6) * 0.15))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_lag_bearish_catchup",
                    "btc_ret": round(current_btc_ret, 3),
                    "sol_ret_3bar": round(sol_ret, 3),
                    "atr_pct": round(atr_pct, 3),
                    "close": closes[-1]
                }
            )

        return None