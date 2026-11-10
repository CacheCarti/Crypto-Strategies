from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetBetaLag(Strategy):
    METADATA = {
        "name": "SolCrossAssetBetaLag",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25 mins)
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_btc_move = 1.6          # 24h delta or fresh shift threshold
        self.max_sol_move = 0.55         # SOL hasn't moved yet (lag condition)
        self.entry_cooldown_bars = 24    # 2 hours cooldown between trades to stay strictly selective
        self.last_trade_bar = -999
        self.entry_bar_idx = -999
        self.max_hold_bars = 5           # 25 min event horizon
        self.prev_btc_ret = None
        self.prev_prev_btc_ret = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        if len(closes) < 30:
            return None

        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Track rolling btc return values
        prev_btc = self.prev_btc_ret if self.prev_btc_ret is not None else current_btc_ret
        prev_prev_btc = self.prev_prev_btc_ret if self.prev_prev_btc_ret is not None else prev_btc

        self.prev_prev_btc_ret = self.prev_btc_ret
        self.prev_btc_ret = current_btc_ret

        # Freshness of BTC move: measure 2-bar shift in 24h return to detect new momentum burst
        btc_shift_2bar = current_btc_ret - prev_prev_btc

        # Manage open position: time-based exit to prevent holding stale lag
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            if bars_held >= self.max_hold_bars:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "event_horizon_expired",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                        "btc_ret": current_btc_ret,
                    }
                )
            return None

        # Cooldown guard: enforce strict selectivity (targeting 30-80 events across 17k bars)
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # SOL local short-term movement over past 3 bars (15 mins)
        sol_ret_3bar = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # ATR for dynamic stop sizing
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or ctx.bar.close == 0:
            return None
        atr_bps = (atr / ctx.bar.close) * 10000.0
        sl_bps = max(60.0, min(120.0, atr_bps * 1.5))
        tp_bps = max(110.0, min(220.0, atr_bps * 2.6))

        # Filter out extreme crisis regimes where correlations break down entirely
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Event Study Long Trigger:
        # 1. BTC makes an explosive upside impulse (fresh shift > 1.6% or total return > 2.0% with strong positive shift)
        # 2. SOL has lagged behind (< 0.55% 15m return)
        btc_bull_event = (btc_shift_2bar > self.min_btc_move) or (current_btc_ret > 2.2 and btc_shift_2bar > 0.8)
        if btc_bull_event and sol_ret_3bar < self.max_sol_move and sol_ret_3bar > -1.2:
            self.entry_bar_idx = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "btc_fresh_lead_long_sol_lag",
                    "btc_shift_2bar": round(btc_shift_2bar, 3),
                    "btc_return_pct": round(current_btc_ret, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                }
            )

        # Event Study Short Trigger:
        # 1. BTC makes an explosive downside drop
        # 2. SOL has not yet caught down (> -0.55% 15m return)
        btc_bear_event = (btc_shift_2bar < -self.min_btc_move) or (current_btc_ret < -2.2 and btc_shift_2bar < -0.8)
        if btc_bear_event and sol_ret_3bar > -self.max_sol_move and sol_ret_3bar < 1.2:
            self.entry_bar_idx = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "btc_fresh_lead_short_sol_lag",
                    "btc_shift_2bar": round(btc_shift_2bar, 3),
                    "btc_return_pct": round(current_btc_ret, 3),
                    "sol_ret_3bar": round(sol_ret_3bar, 3),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index