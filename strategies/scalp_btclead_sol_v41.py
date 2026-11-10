from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import collections

class SolBetaLagScalp(Strategy):
    METADATA = {
        "name": "SolBetaLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25 mins)
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5
        self.btc_ret_history = collections.deque(maxlen=10)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(20)
        if len(closes) < 20:
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)

        if len(self.btc_ret_history) < 4:
            return None

        # Manage open position timeout (scalp holding duration ~ 3-5 bars)
        if ctx.has_position():
            bars_in_trade = ctx.bar_index - self.entry_bar
            if bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_based_scalp_exit",
                        "bars_in_trade": bars_in_trade,
                        "current_sol_close": closes[-1],
                    }
                )
            return None

        # Cooldown guard: prevent overtrading and fee-burn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # BTC Sharp Impulse Definition:
        # 1. 24h return magnitude > 1.8%
        # 2. Fresh 3-bar delta in 24h return to ensure impulse is happening right now, not stale
        fresh_btc_delta = current_btc_ret - self.btc_ret_history[-4]

        # SOL local return over the same 3 bars (lag check)
        sol_return_3bar = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Regime safety: avoid entering during extreme crash meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.8:
            return None

        # Bullish Catch-up:
        # BTC strong upward thrust (> +0.55% acceleration over 3 bars, BTC 24h > 1.8%)
        # SOL lagging significantly behind (moved less than 0.25% in the last 3 bars)
        if current_btc_ret > 1.8 and fresh_btc_delta > 0.55 and sol_return_3bar < 0.25:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_btc_lead_bullish_lag",
                    "btc_24h_ret": current_btc_ret,
                    "fresh_btc_delta": fresh_btc_delta,
                    "sol_ret_3bar": sol_return_3bar,
                    "sol_price": closes[-1],
                }
            )

        # Bearish Catch-up:
        # BTC strong downward thrust (< -0.55% acceleration over 3 bars, BTC 24h < -1.8%)
        # SOL lagging significantly (has not fallen yet, moved > -0.25% in last 3 bars)
        if current_btc_ret < -1.8 and fresh_btc_delta < -0.55 and sol_return_3bar > -0.25:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_btc_lead_bearish_lag",
                    "btc_24h_ret": current_btc_ret,
                    "fresh_btc_delta": fresh_btc_delta,
                    "sol_ret_3bar": sol_return_3bar,
                    "sol_price": closes[-1],
                }
            )

        return None