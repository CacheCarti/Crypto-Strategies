from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLeadLagScalp(Strategy):
    METADATA = {
        "name": "SolBtcLeadLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,  # ~4 bars of 5m
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.last_trade_bar = -50
        self.entry_bar = 0

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

        current_idx = ctx.bar_index

        # Position management: time-based exit after 3-5 bars to prevent bleeding
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            if bars_held >= 4:
                self.last_trade_bar = current_idx
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_exit_catchup_completed",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Mandatory cooldown after last trade
        if current_idx - self.last_trade_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdowns/extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # BTC momentum feature
        btc_ret_24h = ctx.features.get("btc_return_pct", 0.0)

        # SOL local short-term return (last 3 bars = 15m)
        sol_ret_15m = (closes[-1] - closes[-4]) / closes[-4] * 100.0

        # Dynamic volatility check: require moderate volatility for sharp catch-up
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # Minimum required ATR to ensure actionable move, but avoid extreme illiquid chop
        if atr_pct < 0.25:
            return None

        # Strong BTC impulse (> 1.6%) with SOL lagging behind (< 0.45% move)
        # Prior evidence failure: unconfirmed stale triggers.
        # Fix: Enforce asymmetric lead-lag disparity & dynamic ATR confirmation
        long_disparity = btc_ret_24h >= 1.60 and sol_ret_15m < 0.40 and sol_ret_15m > -0.60
        short_disparity = btc_ret_24h <= -1.60 and sol_ret_15m > -0.40 and sol_ret_15m < 0.60

        if long_disparity:
            self.entry_bar = current_idx
            self.last_trade_bar = current_idx
            confidence = min(0.85, 0.55 + abs(btc_ret_24h) * 0.08)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_impulse_sol_lag_long",
                    "btc_return_pct": btc_ret_24h,
                    "sol_ret_15m": sol_ret_15m,
                    "atr_pct": atr_pct,
                    "close": ctx.bar.close,
                }
            )

        if short_disparity:
            self.entry_bar = current_idx
            self.last_trade_bar = current_idx
            confidence = min(0.85, 0.55 + abs(btc_ret_24h) * 0.08)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "btc_impulse_sol_lag_short",
                    "btc_return_pct": btc_ret_24h,
                    "sol_ret_15m": sol_ret_15m,
                    "atr_pct": atr_pct,
                    "close": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index