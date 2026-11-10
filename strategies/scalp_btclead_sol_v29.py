from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 1200,  # ~4 bars (20 mins)
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_trade_bar = -999
        self.max_hold_bars = 4
        self.entry_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(20)
        if len(closes) < 20:
            return None

        # Manage open position exit timing (explicit 3-5 bar event horizon)
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            if bars_in_pos >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_horizon_reached", "bars_held": bars_in_pos}
                )
            return None

        # Hard cooldown enforcement
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Cross-asset BTC lead impulse tracking
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        
        # Calculate recent SOL short-term impulse over 3 bars (15 mins)
        sol_ret_3b = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # Event Study Boundary:
        # BTC exhibits strong directional 24h/session impulse, but SOL has lagged or under-reacted
        # Use threshold tuned to capture distinct market regimes (~30-80 events over 17k bars)
        btc_threshold = 1.6
        sol_lag_threshold = 0.55

        # Check market regime to avoid entering in absolute meltdown
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.75:
            return None

        # Bullish Lead: BTC surging, SOL has not yet matched the move
        if btc_ret > btc_threshold and sol_ret_3b < sol_lag_threshold:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.5 + (btc_ret - btc_threshold) * 0.1)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=75.0,
                take_profit_bps=140.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_ret": round(btc_ret, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "spread_pct": round(btc_ret - sol_ret_3b, 3),
                }
            )

        # Bearish Lead: BTC dumping, SOL has not broken down yet
        if btc_ret < -btc_threshold and sol_ret_3b > -sol_lag_threshold:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.5 + (abs(btc_ret) - btc_threshold) * 0.1)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=75.0,
                take_profit_bps=140.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_ret": round(btc_ret, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "spread_pct": round(btc_ret - sol_ret_3b, 3),
                }
            )

        return None