from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,   # ~4 bars of 5m
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
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
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_exit_reached",
                        "bars_held": bars_held,
                        "price": ctx.bar.close,
                    }
                )
            return None

        # Check cooldown period to prevent overtrading
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Cross-asset feature: BTC 24h return percentage
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        
        # Avoid MELTDOWN or extreme crisis to prevent false lag fills
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate SOL's short-term momentum (over the last 6 bars = 30 minutes)
        sol_ret_30m = ((closes[-1] - closes[-7]) / closes[-7]) * 100.0

        # Calculate local volatility via ATR to confirm normal/expansion regime
        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or closes[-1] == 0:
            return None
        atr_bps = (atr_val / closes[-1]) * 10000.0

        # Only trade when SOL volatility is adequate (not dead, not completely chaotic)
        if not (15.0 <= atr_bps <= 90.0):
            return None

        # Divergence / Beta Catch-up Setup:
        # 1. BTC has a significant positive/negative momentum (|btc_ret| >= 1.6%)
        # 2. SOL has materially lagged (< 0.35% absolute move in the same direction)
        # 3. Bar price confirmation: recent bar must show initial ignition in BTC direction
        bar_body_bps = ((closes[-1] - closes[-2]) / closes[-2]) * 10000.0

        # LONG Setup: BTC strongly bullish, SOL has lagged, recent tick confirms upward shift
        if btc_ret >= 1.60 and sol_ret_30m <= 0.35 and bar_body_bps > 2.0:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.85, 0.55 + (btc_ret - 1.60) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_return_pct": btc_ret,
                    "sol_ret_30m": sol_ret_30m,
                    "atr_bps": atr_bps,
                    "bar_body_bps": bar_body_bps,
                }
            )

        # SHORT Setup: BTC strongly bearish, SOL has lagged, recent tick confirms downward shift
        if btc_ret <= -1.60 and sol_ret_30m >= -0.35 and bar_body_bps < -2.0:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.85, 0.55 + abs(btc_ret + 1.60) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_return_pct": btc_ret,
                    "sol_ret_30m": sol_ret_30m,
                    "atr_bps": atr_bps,
                    "bar_body_bps": bar_body_bps,
                }
            )

        return None