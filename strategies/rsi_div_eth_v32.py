from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # 6h hold target for 1h swing
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 28
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains[i] = diff
            else:
                losses[i] = -diff

        avg_gain = sum(gains[1:period + 1]) / period
        avg_loss = sum(losses[1:period + 1]) / period

        rsis = [None] * period
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rsis.append(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))

        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100.0 - (100.0 / (1.0 + rs)))
        return rsis

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.lookback + self.rsi_period + 5
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        # Calculate RSI series
        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback + 2:
            return None

        curr_rsi = rsi_series[-1]
        if curr_rsi is None:
            return None

        # Position management: normalize exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_rsi >= 60.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": round(curr_rsi, 2)})
            elif pos_dir == "short" and curr_rsi <= 40.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": round(curr_rsi, 2)})
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis / meltdown to protect mean-reverting divergences
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Detect local pivots over lookback window (excluding current bar)
        # We search window: [-lookback-1 : -1]
        c_win = closes[-self.lookback - 1 : -1]
        r_win = [r for r in rsi_series[-self.lookback - 1 : -1] if r is not None]
        if len(c_win) != len(r_win) or len(c_win) < 15:
            return None

        # Identify recent pivot lows/highs using 3-bar local extremum
        low_pivots = []
        high_pivots = []
        for i in range(1, len(c_win) - 1):
            if c_win[i] <= c_win[i - 1] and c_win[i] <= c_win[i + 1]:
                low_pivots.append((c_win[i], r_win[i]))
            if c_win[i] >= c_win[i - 1] and c_win[i] >= c_win[i + 1]:
                high_pivots.append((c_win[i], r_win[i]))

        curr_close = closes[-1]

        # Bullish Divergence: Current price creates new swing low (or near it) with higher RSI
        if curr_rsi < 40.0 and len(low_pivots) >= 1:
            # Prior trough is the lowest pivot detected
            prior_min_price, prior_rsi = min(low_pivots, key=lambda x: x[0])
            if curr_close < prior_min_price and curr_rsi > prior_rsi + 3.0:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_rsi_div_regime_aligned",
                        "curr_price": round(curr_close, 2),
                        "prior_price": round(prior_min_price, 2),
                        "curr_rsi": round(curr_rsi, 2),
                        "prior_rsi": round(prior_rsi, 2),
                        "regime": regime,
                    }
                )

        # Bearish Divergence: Current price creates new swing high with lower RSI
        if curr_rsi > 60.0 and len(high_pivots) >= 1:
            prior_max_price, prior_rsi = max(high_pivots, key=lambda x: x[0])
            if curr_close > prior_max_price and curr_rsi < prior_rsi - 3.0:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_rsi_div_regime_aligned",
                        "curr_price": round(curr_close, 2),
                        "prior_price": round(prior_max_price, 2),
                        "curr_rsi": round(curr_rsi, 2),
                        "prior_rsi": round(prior_rsi, 2),
                        "regime": regime,
                    }
                )

        return None