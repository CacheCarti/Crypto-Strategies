from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import collections

class BtcSolBetaLagScalp(Strategy):
    METADATA = {
        "name": "BtcSolBetaLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 165.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 4
        self.min_btc_delta = 1.6
        self.max_sol_move = 0.55

        self.last_exit_bar = -999
        self.entry_bar = -999
        self.btc_ret_history = collections.deque(maxlen=10)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(15)
        if len(closes) < 15:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(btc_ret)

        if len(self.btc_ret_history) < 4:
            return None

        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                pos_dir = ctx.position_direction() or "long"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "scalp_time_decay_exit",
                        "bars_held": bars_in_trade,
                        "pos_dir": pos_dir,
                        "close": ctx.bar.close
                    }
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        fresh_btc_move = self.btc_ret_history[-1] - self.btc_ret_history[-4]

        c_now = closes[-1]
        c_prior = closes[-4]
        if c_prior == 0:
            return None
        sol_recent_pct = ((c_now - c_prior) / c_prior) * 100.0

        if fresh_btc_move >= self.min_btc_delta and sol_recent_pct < self.max_sol_move:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_expansion_sol_lag_long",
                    "btc_delta_3b": round(fresh_btc_move, 3),
                    "sol_move_3b": round(sol_recent_pct, 3),
                    "btc_ret": round(btc_ret, 3),
                    "close": c_now
                }
            )

        if fresh_btc_move <= -self.min_btc_delta and sol_recent_pct > -self.max_sol_move:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_expansion_sol_lag_short",
                    "btc_delta_3b": round(fresh_btc_move, 3),
                    "sol_move_3b": round(sol_recent_pct, 3),
                    "btc_ret": round(btc_ret, 3),
                    "close": c_now
                }
            )

        return None