from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.pivot_window = 30
        self.cooldown_bars = 12
        self.last_trade_bar = -100
        self.max_hold_bars = 18

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_vals = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rs = avg_gain / avg_loss if avg_loss != 0 else float('inf')
        rsi_vals.append(100.0 - (100.0 / (1.0 + rs)) if avg_loss != 0 else 100.0)

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_vals.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_vals

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(55)
        if len(closes) < 50:
            return None

        # Check regime gate: avoid crisis / extreme meltdown to fix prior OOS failure
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "regime": market_regime})
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.pivot_window:
            return None

        curr_rsi = rsi_series[-1]
        curr_price = closes[-1]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_trade_bar
            
            # Normalization exit
            if pos_dir == "long" and curr_rsi >= 58.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_norm_long", "rsi": curr_rsi})
            elif pos_dir == "short" and curr_rsi <= 42.0:
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_norm_short", "rsi": curr_rsi})
            
            # Time-based stop
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop", "bars_held": bars_held})
            return None

        # Entry logic: enforce cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Analyze past 30 bars (excluding current bar)
        hist_closes = closes[-self.pivot_window:-1]
        hist_rsi = rsi_series[-self.pivot_window:-1]

        min_price = min(hist_closes)
        min_idx = hist_closes.index(min_price)
        prior_rsi_at_low = hist_rsi[min_idx]

        max_price = max(hist_closes)
        max_idx = hist_closes.index(max_price)
        prior_rsi_at_high = hist_rsi[max_idx]

        # Bullish Divergence: Price makes new low, but RSI makes higher low and is in oversold/rebound territory
        if curr_price < min_price and curr_rsi > prior_rsi_at_low and curr_rsi < 40.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_div_regime_ok",
                    "curr_price": curr_price,
                    "min_price": min_price,
                    "curr_rsi": curr_rsi,
                    "prior_rsi": prior_rsi_at_low
                }
            )

        # Bearish Divergence: Price makes new high, but RSI makes lower high and is in overbought/exhaustion territory
        if curr_price > max_price and curr_rsi < prior_rsi_at_high and curr_rsi > 60.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_div_regime_ok",
                    "curr_price": curr_price,
                    "max_price": max_price,
                    "curr_rsi": curr_rsi,
                    "prior_rsi": prior_rsi_at_high
                }
            )

        return None