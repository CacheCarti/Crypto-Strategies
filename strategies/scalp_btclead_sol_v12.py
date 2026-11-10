from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolBtcLeadLagScalp(Strategy):
    METADATA = {
        "name": "SolBtcLeadLagScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,  # ~4 bars (20 mins)
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 14
        self.max_hold_bars = 4
        self.bars_in_trade = 0
        self.last_exit_bar = -100
        self.entry_price = 0.0

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
        self.bars_in_trade = 0
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        # Position tracking & explicit time-based exit
        if ctx.has_position():
            self.bars_in_trade += 1
            pos_dir = ctx.position_direction()
            
            # Fast time-based exit for scalp
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_reached", "bars_held": self.bars_in_trade}
                )
            
            # Quick profit lock-in / trend exhaust
            curr_c = ctx.bar.close
            if pos_dir == "long" and self.entry_price > 0:
                bps_gain = ((curr_c - self.entry_price) / self.entry_price) * 10000.0
                if bps_gain >= 120.0:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "early_profit_lock", "bps": bps_gain})
            elif pos_dir == "short" and self.entry_price > 0:
                bps_gain = ((self.entry_price - curr_c) / self.entry_price) * 10000.0
                if bps_gain >= 120.0:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "early_profit_lock", "bps": bps_gain})

            return None

        # Cooldown guard to avoid overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # BTC Lead Feature
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # SOL local return over last 3 bars (15 mins) and 6 bars (30 mins)
        sol_ret_3b = ((closes[-1] - closes[-4]) / closes[-4]) * 100.0
        sol_ret_6b = ((closes[-1] - closes[-7]) / closes[-7]) * 100.0

        # Regime & Volatility Check
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or closes[-1] == 0:
            return None
        atr_pct = (atr / closes[-1]) * 100.0

        # Filter out dead markets or extreme meltdown
        if atr_pct < 0.15 or atr_pct > 1.8:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN":
            return None

        # Setup: BTC has a sharp impulse (> 1.5%), but SOL has lagged (< 0.5% in same window)
        # Dynamic threshold based on lead-lag divergence
        btc_threshold = 1.6
        lag_threshold = 0.45

        # Check for Long catch-up: BTC surged up, SOL hasn't moved yet or barely budged
        if btc_ret > btc_threshold and sol_ret_3b < lag_threshold and sol_ret_6b < 0.8:
            divergence = btc_ret - sol_ret_3b
            confidence = min(0.85, 0.55 + (divergence / 10.0))
            self.entry_price = closes[-1]
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=95.0,
                take_profit_bps=160.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_bull",
                    "btc_ret": round(btc_ret, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "divergence": round(divergence, 3),
                    "atr_pct": round(atr_pct, 4)
                }
            )

        # Check for Short catch-up: BTC dropped sharply, SOL hasn't dumped yet
        if btc_ret < -btc_threshold and sol_ret_3b > -lag_threshold and sol_ret_6b > -0.8:
            divergence = abs(btc_ret - sol_ret_3b)
            confidence = min(0.85, 0.55 + (divergence / 10.0))
            self.entry_price = closes[-1]
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=95.0,
                take_profit_bps=160.0,
                horizon_seconds=1200,
                metadata={
                    "reason": "btc_lead_sol_lag_bear",
                    "btc_ret": round(btc_ret, 3),
                    "sol_ret_3b": round(sol_ret_3b, 3),
                    "divergence": round(divergence, 3),
                    "atr_pct": round(atr_pct, 4)
                }
            )

        return None