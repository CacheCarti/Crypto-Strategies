from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCrossAssetLagScalp(Strategy):
    METADATA = {
        "name": "SolCrossAssetLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 10
        self.max_hold_bars = 5
        self.bars_in_position = 0
        self.last_exit_bar = -999
        self.btc_ret_history = []
        self.lead_threshold = 1.4
        self.lag_max_sol = 0.55

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25)
        highs = ctx.highs(25)
        lows = ctx.lows(25)
        if len(closes) < 20:
            return None

        # Track BTC 24h return momentum to detect sudden impulse
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        self.btc_ret_history.append(btc_ret)
        if len(self.btc_ret_history) > 10:
            self.btc_ret_history.pop(0)

        # Position management: time-based exit to keep scalps tight
        if ctx.has_position():
            self.bars_in_position += 1
            if self.bars_in_position >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "max_bars_timeout", "bars_held": self.bars_in_position})
            return None
        else:
            self.bars_in_position = 0

        # Cooldown guard after last trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Filter extreme crisis regimes to prevent toxic fills
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.btc_ret_history) < 4:
            return None

        # Delta of BTC 24h return over 3 bars (~15m surge)
        btc_shock = self.btc_ret_history[-1] - self.btc_ret_history[-4]
        
        # Local SOL move over 3 bars in percent
        sol_ret_3 = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0

        # ATR for volatility normalization and baseline noise filter
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # Bullish cross-asset lag setup: BTC jumped hard, but SOL hasn't moved aggressively yet
        if btc_shock >= self.lead_threshold and abs(sol_ret_3) <= self.lag_max_sol and atr_pct > 0.15:
            # Check price is not in severe micro-downtrend (avoid buying into local collapse)
            if closes[-1] >= lows[-2]:
                confidence = min(0.85, 0.55 + (abs(btc_shock) - self.lead_threshold) * 0.15)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "btc_lead_sol_lag_bull",
                        "btc_shock": round(btc_shock, 3),
                        "sol_ret_3": round(sol_ret_3, 3),
                        "atr_pct": round(atr_pct, 4)
                    }
                )

        # Bearish cross-asset lead: BTC plunged hard, SOL has not collapsed yet
        if btc_shock <= -self.lead_threshold and abs(sol_ret_3) <= self.lag_max_sol and atr_pct > 0.15:
            if closes[-1] <= highs[-2]:
                confidence = min(0.85, 0.55 + (abs(btc_shock) - self.lead_threshold) * 0.15)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "btc_lead_sol_lag_bear",
                        "btc_shock": round(btc_shock, 3),
                        "sol_ret_3": round(sol_ret_3, 3),
                        "atr_pct": round(atr_pct, 4)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0