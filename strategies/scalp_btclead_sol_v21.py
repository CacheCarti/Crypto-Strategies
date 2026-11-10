from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaLag(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaLag",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25m) max intended hold
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_trade_bar = -50
        self.entry_bar = -1
        self.min_btc_move = 1.30        # BTC 24h return jump magnitude
        self.max_sol_move = 0.50        # SOL move threshold (unrealized beta)
        self.btc_ret_history = []
        self.max_hold_bars = 5

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        # Track rolling btc_return_pct
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(btc_ret)
        if len(self.btc_ret_history) > 10:
            self.btc_ret_history.pop(0)

        # Position exit management (time-based scalp decay)
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            if bars_in_pos >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "scalp_time_decay_exit",
                        "bars_held": bars_in_pos,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard to avoid overtrading and friction drag
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime safety filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Stricter event boundary: measure the FRESH impulse over 3 bars (15 mins)
        if len(self.btc_ret_history) < 4:
            return None

        btc_delta_3b = self.btc_ret_history[-1] - self.btc_ret_history[-4]

        # SOL price move over the same 3 bars in percent
        sol_ret_3b = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # ATR filter: avoid trading dead chop
        atr = self._atr(ctx.highs(20), ctx.lows(20), closes, 14)
        if atr <= 0 or (atr / closes[-1]) < 0.0010:
            return None

        # Long Beta Lag: BTC surged sharply, SOL lagged behind
        if btc_delta_3b >= self.min_btc_move and sol_ret_3b <= self.max_sol_move and sol_ret_3b >= -0.80:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.90, max(0.60, 0.60 + (btc_delta_3b - self.min_btc_move) * 0.15))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=90.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_delta_3b": round(btc_delta_3b, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "sol_price": ctx.bar.close,
                    "atr": round(atr, 4)
                }
            )

        # Short Beta Lag: BTC dumped sharply, SOL lagged behind
        if btc_delta_3b <= -self.min_btc_move and sol_ret_3b >= -self.max_sol_move and sol_ret_3b <= 0.80:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            conf = min(0.90, max(0.60, 0.60 + (abs(btc_delta_3b) - self.min_btc_move) * 0.15))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=90.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_delta_3b": round(btc_delta_3b, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "sol_price": ctx.bar.close,
                    "atr": round(atr, 4)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index