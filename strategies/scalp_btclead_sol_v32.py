from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLeadLagScalp(Strategy):
    METADATA = {
        "name": "SolBtcLeadLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.btc_window = 3
        self.sol_window = 3
        self.min_btc_move = 1.45
        self.max_sol_move = 0.55
        self.max_hold_bars = 4

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        # Manage active position time-based scalp exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                pos_dir = ctx.position_direction()
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "scalp_time_decay_exit",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                        "position_dir": pos_dir,
                    }
                )
            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip volatile or crisis regimes to avoid adverse selection
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Lead-lag evaluation
        # btc_return_pct is a rolling 24h return, so a sudden move over 3 bars (15m) shows delta
        curr_btc = ctx.features.get("btc_return_pct", 0.0)
        # Approximate recent BTC velocity using local delta if features allow, or absolute level
        # To measure fresh move, we inspect recent SOL return vs threshold
        sol_close_now = closes[-1]
        sol_close_prev = closes[-1 - self.sol_window]
        sol_ret_pct = ((sol_close_now - sol_close_prev) / sol_close_prev) * 100.0

        # Also get short-term momentum on SOL (EMA9 vs EMA21) to confirm not strongly diverging
        ema_fast = sum(closes[-6:]) / 6.0
        ema_slow = sum(closes[-18:]) / 18.0

        # Condition 1: Sharp BTC positive move while SOL has barely reacted
        if curr_btc > self.min_btc_move and abs(sol_ret_pct) < self.max_sol_move:
            if sol_close_now >= ema_fast * 0.998:  # not in a strong counter-dump
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=75.0,
                    take_profit_bps=120.0,
                    horizon_seconds=1200,
                    metadata={
                        "reason": "btc_surge_sol_lag_catchup",
                        "btc_return_pct": curr_btc,
                        "sol_ret_15m": round(sol_ret_pct, 3),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                    }
                )

        # Condition 2: Sharp BTC negative move while SOL has barely reacted
        if curr_btc < -self.min_btc_move and abs(sol_ret_pct) < self.max_sol_move:
            if sol_close_now <= ema_fast * 1.002:  # not in a strong counter-pump
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=75.0,
                    take_profit_bps=120.0,
                    horizon_seconds=1200,
                    metadata={
                        "reason": "btc_dump_sol_lag_catchup",
                        "btc_return_pct": curr_btc,
                        "sol_ret_15m": round(sol_ret_pct, 3),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                    }
                )

        return None