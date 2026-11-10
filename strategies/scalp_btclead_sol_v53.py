from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcCatchupScalp(Strategy):
    METADATA = {
        "name": "SolBtcCatchupScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 1200,   # 4 bars @ 5m
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_trade_bar = -50
        self.entry_bar = -1
        self.btc_window = 3               # 15m delta
        self.sol_window = 3
        self.btc_impulse_threshold = 0.60 # 0.60% move in 15m is a sharp fresh move
        self.sol_lag_ratio = 0.35         # SOL has moved less than 35% of BTC's move

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage existing position with time-based exit (hold 3-5 bars)
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= 4:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_horizon_exit", "bars_held": bars_held, "close": ctx.bar.close}
                )
            return None

        # Mandatory cooldown between trades to avoid friction death
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime gate: Prior failures showed weak edge in choppy/crisis regimes.
        # Restrict entries strictly to NORMAL or CALM regimes.
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime not in ("NORMAL", "calm"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.25:
            return None

        # BTC momentum & freshness check
        btc_ret_curr = ctx.features.get("btc_return_pct", 0.0)
        # We track recent BTC returns via feature changes across bars
        # Store rolling btc_returns on self
        if not hasattr(self, "btc_ret_hist"):
            self.btc_ret_hist = []
        self.btc_ret_hist.append(btc_ret_curr)
        if len(self.btc_ret_hist) > 20:
            self.btc_ret_hist.pop(0)

        if len(self.btc_ret_hist) < self.btc_window + 1:
            return None

        # Fresh impulse: BTC 24h return shifted over 3 bars (15m change in 24h return is 15m return)
        btc_delta = self.btc_ret_hist[-1] - self.btc_ret_hist[-1 - self.btc_window]
        
        # SOL return over the same 3-bar window
        sol_ret = ((closes[-1] - closes[-1 - self.sol_window]) / closes[-1 - self.sol_window]) * 100.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None

        # Check for sharp BTC surge with SOL lagging
        if btc_delta > self.btc_impulse_threshold:
            # Long catch-up setup: BTC surged up, but SOL hasn't run yet
            if sol_ret < (btc_delta * self.sol_lag_ratio):
                self.last_trade_bar = ctx.bar_index
                self.entry_bar = ctx.bar_index
                conf = min(0.9, max(0.6, 0.5 + (btc_delta - sol_ret) * 0.2))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "btc_lead_sol_lag_bull",
                        "btc_delta_15m": round(btc_delta, 3),
                        "sol_ret_15m": round(sol_ret, 3),
                        "atr": round(atr, 4),
                        "close": ctx.bar.close
                    }
                )

        # Check for sharp BTC dump with SOL lagging
        elif btc_delta < -self.btc_impulse_threshold:
            # Short catch-up setup: BTC dumped, SOL hasn't dumped yet
            if sol_ret > (btc_delta * self.sol_lag_ratio):
                self.last_trade_bar = ctx.bar_index
                self.entry_bar = ctx.bar_index
                conf = min(0.9, max(0.6, 0.5 + abs(btc_delta - sol_ret) * 0.2))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "btc_lead_sol_lag_bear",
                        "btc_delta_15m": round(btc_delta, 3),
                        "sol_ret_15m": round(sol_ret, 3),
                        "atr": round(atr, 4),
                        "close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index