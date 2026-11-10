from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 14
        self.last_exit_bar = -100
        self.btc_window = 3
        self.entry_bar = -100

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 25 or ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Position exit logic: max hold 4 bars (~20 mins) or quick reversal
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()
            
            # Time-based scalp exit
            if bars_held >= 4:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_horizon_exit", "bars_held": bars_held})
            
            # Adverse move cut (momentum failed to catch up)
            entry_px = ctx.position.get("entry_price", closes[-1]) if ctx.position else closes[-1]
            pnl_bps = ((closes[-1] - entry_px) / entry_px) * 10000.0 if pos_dir == "long" else ((entry_px - closes[-1]) / entry_px) * 10000.0
            if bars_held >= 2 and pnl_bps < -45.0:
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "adverse_momentum_bail", "pnl_bps": pnl_bps})
            return None

        # Cooldown guard: enforce post-trade quiet period to prevent trade clustering
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdowns or extreme crisis
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        # Feature lookup: BTC 24h return percentage
        btc_ret_now = ctx.features.get("btc_return_pct", 0.0)

        # Approximate BTC delta over 3 bars (15m window)
        # In platform features, btc_return_pct updates per bar. Sharp spikes cause rapid acceleration in this metric.
        # We also measure SOL's return over the same 3 bars.
        sol_ret_3b = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0
        
        # Volatility normalization via ATR (bps of price)
        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val <= 0:
            return None
        atr_bps = (atr_val / closes[-1]) * 10000.0

        # BTC momentum threshold: sharp move in BTC return (> 1.2% divergence vs SOL)
        # Catch-up condition: BTC moved significantly in 24h/short horizon, but SOL has lagged (< 0.35% move in last 15m)
        lead_lag_threshold = 1.35
        lag_cutoff = 0.35

        # Long Catch-up Setup: BTC strong bull expansion, SOL quiet consolidation ready to break
        if btc_ret_now >= lead_lag_threshold and abs(sol_ret_3b) <= lag_cutoff:
            # Price action confirmation: SOL close is above open of current bar (receptive to bounce)
            if ctx.bar.close >= ctx.bar.open:
                sl_bps = max(60.0, min(110.0, atr_bps * 1.2))
                tp_bps = max(110.0, min(220.0, atr_bps * 2.2))
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=900,
                    metadata={
                        "reason": "btc_lead_sol_lag_long",
                        "btc_ret": btc_ret_now,
                        "sol_ret_3b": sol_ret_3b,
                        "atr_bps": atr_bps,
                    }
                )

        # Short Catch-up Setup: BTC steep plunge, SOL has not yet repriced lower
        if btc_ret_now <= -lead_lag_threshold and abs(sol_ret_3b) <= lag_cutoff:
            if ctx.bar.close <= ctx.bar.open:
                sl_bps = max(60.0, min(110.0, atr_bps * 1.2))
                tp_bps = max(110.0, min(220.0, atr_bps * 2.2))
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=900,
                    metadata={
                        "reason": "btc_lead_sol_lag_short",
                        "btc_ret": btc_ret_now,
                        "sol_ret_3b": sol_ret_3b,
                        "atr_bps": atr_bps,
                    }
                )

        return None