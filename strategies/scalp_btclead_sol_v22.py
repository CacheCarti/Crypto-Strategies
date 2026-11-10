from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcBetaCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcBetaCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_bar = -1
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.max_hold_bars = 5
        self.trailing_arm_bps = 50.0
        self.trailing_step_bps = 25.0
        self.peak_return_bps = 0.0

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
        self.entry_bar = -1
        self.peak_return_bps = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 25:
            return None

        current_price = ctx.bar.close

        # Position Management
        if ctx.has_position():
            pos = ctx.position
            if pos is None:
                return None
            direction = pos.get("direction", "long")
            entry_price = pos.get("entry_price", current_price)
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            if direction == "long":
                ret_bps = ((current_price - entry_price) / entry_price) * 10000.0
            else:
                ret_bps = ((entry_price - current_price) / entry_price) * 10000.0

            if ret_bps > self.peak_return_bps:
                self.peak_return_bps = ret_bps

            # Time-based exit to capture quick beta lag
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "max_bars_reached",
                    "ret_bps": ret_bps,
                    "bars_held": bars_held
                })

            # Trailing profit lock to secure alpha
            if self.peak_return_bps >= self.trailing_arm_bps:
                if self.peak_return_bps - ret_bps >= self.trailing_step_bps:
                    return ctx.signal("flat", confidence=0.8, metadata={
                        "reason": "trailing_lock_profit",
                        "peak_bps": self.peak_return_bps,
                        "ret_bps": ret_bps
                    })

            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid crisis & meltdown states
        mkt_regime = ctx.market.get("regime", "NORMAL")
        if mkt_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset BTC impulse & SOL lag logic
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        sol_ret_3 = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # Dislocation threshold: BTC 24h strong push and SOL 15m consolidation/lag
        long_trigger = (btc_ret >= 1.7) and (sol_ret_3 <= 0.45)
        short_trigger = (btc_ret <= -1.7) and (sol_ret_3 >= -0.45)

        if not (long_trigger or short_trigger):
            return None

        # Volatility check: require sufficient ATR to cover friction
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or (atr / current_price) < 0.0012:
            return None

        # Local breakout confirmation (SOL shouldn't be strictly counter-trending violently)
        if long_trigger:
            if current_price < closes[-2]:
                return None
            self.entry_bar = ctx.bar_index
            self.peak_return_bps = 0.0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_long",
                    "btc_ret": btc_ret,
                    "sol_ret_3": sol_ret_3,
                    "atr": atr,
                    "price": current_price
                }
            )

        if short_trigger:
            if current_price > closes[-2]:
                return None
            self.entry_bar = ctx.bar_index
            self.peak_return_bps = 0.0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_lead_sol_lag_short",
                    "btc_ret": btc_ret,
                    "sol_ret_3": sol_ret_3,
                    "atr": atr,
                    "price": current_price
                }
            )

        return None