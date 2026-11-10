from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars of 5m
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 8
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.entry_price = 0.0
        self.btc_window = 3

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Position tracking / Time-based exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "max_bars_expired", "bars_held": bars_held})
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: reject meltdown/crisis panics where beta breaks down
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Feature tracking: fresh BTC impulse
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        
        # Calculate SOL short-term return over last 3 bars (15m)
        sol_ret_3b = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # We look for a sharp divergence: BTC has moved significantly (> 1.6%)
        # but SOL has lagged (< 0.5% in the same direction or even counter-trend)
        # To avoid stale moves, verify ATR/volatility is reasonable
        highs = ctx.highs(14)
        lows = ctx.lows(14)
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(highs))]
        atr = sum(trs[-10:]) / 10.0 if trs else 0.0
        if closes[-1] > 0 and (atr / closes[-1]) > 0.03:
            return None  # Excessive SOL volatility / outlier bar

        signal_dir = None
        spread = 0.0

        if btc_ret >= 1.60 and sol_ret_3b <= 0.45:
            signal_dir = "long"
            spread = btc_ret - sol_ret_3b
        elif btc_ret <= -1.60 and sol_ret_3b >= -0.45:
            signal_dir = "short"
            spread = sol_ret_3b - btc_ret

        if signal_dir:
            self.entry_bar = ctx.bar_index
            self.entry_price = ctx.bar.close
            conf = min(0.90, max(0.60, 0.50 + abs(spread) * 0.10))
            return ctx.signal(
                signal_dir,
                confidence=conf,
                stop_loss_bps=95.0,
                take_profit_bps=160.0,
                horizon_seconds=1500,
                metadata={
                    "reason": f"btc_lead_sol_lag_{signal_dir}",
                    "btc_return_pct": round(btc_ret, 3),
                    "sol_return_3b": round(sol_ret_3b, 3),
                    "spread_pct": round(spread, 3),
                    "close": closes[-1]
                }
            )

        return None