from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.max_hold_bars = 16

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsi_series = []
        for end_idx in range(period, len(closes)):
            avg_gain = sum(gains[end_idx - period:end_idx]) / period
            avg_loss = sum(losses[end_idx - period:end_idx]) / period
            if avg_loss == 0.0:
                rsi_val = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_val = 100.0 - (100.0 / (1.0 + rs))
            rsi_series.append(rsi_val)
        return rsi_series

    def _detect_divergence(self, closes: list, rsis: list):
        # Look back over recent bars for pivots
        # Need at least 25 bars of history
        if len(closes) < 26 or len(rsis) < 26:
            return None

        # Current values
        curr_price = closes[-1]
        curr_rsi = rsis[-1]

        # Scan for swing low in price between 6 and 25 bars ago
        window_closes = closes[-25:-5]
        window_rsis = rsis[-25:-5]

        min_price = min(window_closes)
        min_p_idx = window_closes.index(min_price)
        rsi_at_min_p = window_rsis[min_p_idx]

        # Bullish Divergence: Price lower low or near-equal, but RSI notably higher low & recovering
        if curr_price < min_price and curr_rsi > rsi_at_min_p + 3.0 and curr_rsi < 45.0:
            return "bullish"

        max_price = max(window_closes)
        max_p_idx = window_closes.index(max_price)
        rsi_at_max_p = window_rsis[max_p_idx]

        # Bearish Divergence: Price higher high, but RSI notably lower high & rolling over
        if curr_price > max_price and curr_rsi < rsi_at_max_p - 3.0 and curr_rsi > 55.0:
            return "bearish"

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsis = self._calc_rsi_series(closes, self.rsi_period)
        if not rsis or len(rsis) < 26:
            return None

        curr_rsi = rsis[-1]
        curr_price = ctx.bar.close

        # Position Management & Exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based exhaustion exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_exhaustion_flat",
                    "bars_held": bars_held,
                    "rsi": round(curr_rsi, 2),
                    "price": curr_price
                })

            # Normalization exits
            if pos_dir == "long" and curr_rsi >= 58.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "rsi_reversion_normalized_long",
                    "bars_held": bars_held,
                    "rsi": round(curr_rsi, 2),
                    "price": curr_price
                })
            elif pos_dir == "short" and curr_rsi <= 42.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "rsi_reversion_normalized_short",
                    "bars_held": bars_held,
                    "rsi": round(curr_rsi, 2),
                    "price": curr_price
                })
            return None

        # Cooldown guard after closing
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime Gate: avoid entering divergences during severe crisis/meltdown where momentum cascade breaks reversions
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Divergence evaluation
        div = self._detect_divergence(closes, rsis)
        if not div:
            return None

        if div == "bullish":
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "rsi": round(curr_rsi, 2),
                    "price": curr_price,
                    "regime": regime,
                    "crisis_score": round(crisis_score, 3)
                }
            )

        if div == "bearish":
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "rsi": round(curr_rsi, 2),
                    "price": curr_price,
                    "regime": regime,
                    "crisis_score": round(crisis_score, 3)
                }
            )

        return None