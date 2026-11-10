from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,  # ~5 bars (25 mins)
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.prev_btc_ret = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -100

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

        if len(closes) < 25:
            return None

        # Manage existing position via time horizon or quick trail
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_horizon_exit",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard: enforce strict spacing to prevent friction death
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime Gate: Only trade when market is normal/calm and crisis score is low
        # Prior evidence failed in turbulent/crisis regimes where cross-asset divergence is structural breakdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.35 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset feature: BTC 24h return movement
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        btc_delta = btc_ret - self.prev_btc_ret
        self.prev_btc_ret = btc_ret

        # Calculate SOL short-term return over the last 3 bars (~15 min lag window)
        sol_ret_3bar = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Calculate ATR to check volatility expansion
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # BTC Sharp Impulse condition (> 1.2% delta or > 2.0% absolute move with fresh acceleration)
        # SOL Lag condition: SOL hasn't caught up relative to BTC impulse
        btc_impulse_long = (btc_delta >= 0.7 or btc_ret >= 1.5) and (sol_ret_3bar < 0.45)
        btc_impulse_short = (btc_delta <= -0.7 or btc_ret <= -1.5) and (sol_ret_3bar > -0.45)

        # Micro-trend confirmation on SOL: ensure we do not catch a totally broken falling knife
        ema_short = sum(closes[-5:]) / 5.0
        ema_mid = sum(closes[-15:]) / 15.0

        if btc_impulse_long and closes[-1] >= ema_mid * 0.997:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=80.0,
                take_profit_bps=150.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_lag_catchup_long",
                    "btc_ret": btc_ret,
                    "btc_delta": btc_delta,
                    "sol_ret_3bar": sol_ret_3bar,
                    "atr_pct": atr_pct,
                }
            )

        if btc_impulse_short and closes[-1] <= ema_mid * 1.003:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=80.0,
                take_profit_bps=150.0,
                horizon_seconds=1500,
                metadata={
                    "reason": "btc_lead_lag_catchup_short",
                    "btc_ret": btc_ret,
                    "btc_delta": btc_delta,
                    "sol_ret_3bar": sol_ret_3bar,
                    "atr_pct": atr_pct,
                }
            )

        return None