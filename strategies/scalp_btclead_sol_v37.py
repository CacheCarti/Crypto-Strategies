from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaLagScalp(Strategy):
    METADATA = {
        "name": "SolBtcBetaLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,  # ~4 bars of 5m
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 14
        self.last_trade_bar = -50
        self.entry_bar = 0
        self.btc_ret_history = []
        self.max_history = 10

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

        # Track BTC 24h return rolling window to detect sharp intra-bar changes
        current_btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(current_btc_ret)
        if len(self.btc_ret_history) > self.max_history:
            self.btc_ret_history.pop(0)

        # Check existing position
        if ctx.has_position():
            bars_in_pos = ctx.bar_index - self.entry_bar
            # Scalp time exit: cut after 4 bars (20 mins) to enforce catch-up brevity
            if bars_in_pos >= 4:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_catchup_expired", "bars_held": bars_in_pos}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only evaluate if we have enough BTC delta history
        if len(self.btc_ret_history) < 4:
            return None

        # Freshness of BTC impulse: delta over last 3 bars (15 mins)
        btc_pulse = self.btc_ret_history[-1] - self.btc_ret_history[-4]

        # Calculate SOL's recent return over the same 3-bar window
        sol_ret_pct = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # Avoid regime breakdown / crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.40:
            return None

        # Volatility check via ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or closes[-1] == 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # Require a sharp fresh BTC impulse (|pulse| > 0.45% in 15m)
        # and significant lag in SOL relative to BTC's impulse (SOL moved less than 35% of BTC)
        min_btc_pulse = 0.45
        direction = None
        lag_spread = 0.0

        if btc_pulse > min_btc_pulse and sol_ret_pct < (btc_pulse * 0.35) and sol_ret_pct > -0.60:
            # BTC popped up, SOL lagging behind but not collapsing
            direction = "long"
            lag_spread = btc_pulse - sol_ret_pct
        elif btc_pulse < -min_btc_pulse and sol_ret_pct > (btc_pulse * 0.35) and sol_ret_pct < 0.60:
            # BTC flushed down, SOL lagging behind but not exploding up
            direction = "short"
            lag_spread = abs(btc_pulse - sol_ret_pct)

        if direction is not None:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            conf = min(0.85, max(0.55, 0.50 + lag_spread * 0.25))

            return ctx.signal(
                direction,
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": f"btc_beta_catchup_{direction}",
                    "btc_pulse_15m": round(btc_pulse, 4),
                    "sol_ret_15m": round(sol_ret_pct, 4),
                    "lag_spread": round(lag_spread, 4),
                    "atr_pct": round(atr_pct, 4),
                    "close": closes[-1],
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index