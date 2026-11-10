from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 10
        self.last_trade_bar = -50
        self.entry_bar = -1
        self.max_hold_bars = 12

    def _rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        rsis = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
        rsis.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100.0 - (100.0 / (1.0 + rs)))
        return rsis

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)
        
        rsis = self._rsi_series(closes, self.rsi_period)
        if len(rsis) < self.lookback:
            return None

        curr_rsi = rsis[-1]
        curr_close = closes[-1]
        bars_since_trade = ctx.bar_index - self.last_trade_bar

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            direction = ctx.position_direction()

            # Dynamic Volatility & RSI Normalization Exits
            if direction == "long":
                if curr_rsi >= 58.0 or bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "rsi_normalized_or_time_stop", "rsi": curr_rsi, "bars_held": bars_held}
                    )
            elif direction == "short":
                if curr_rsi <= 42.0 or bars_held >= self.max_hold_bars:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "rsi_normalized_or_time_stop", "rsi": curr_rsi, "bars_held": bars_held}
                    )
            return None

        # Entry logic with mandatory post-trade cooldown
        if bars_since_trade < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Evaluate confirmed local pivot low and high over lookback window
        # Pivot low logic
        window_lows = lows[-self.lookback:]
        window_rsis = rsis[-self.lookback:]
        min_low_idx = window_lows.index(min(window_lows))
        
        # Bullish Divergence: Price made recent local low, but RSI formed a higher trough
        # The prior swing trough must be mature (at least 5 bars ago and not the immediate current bar)
        if 4 <= min_low_idx <= self.lookback - 6:
            prior_trough_price = window_lows[min_low_idx]
            prior_trough_rsi = window_rsis[min_low_idx]
            recent_low = min(window_lows[min_low_idx + 4:])
            recent_rsi = min(window_rsis[min_low_idx + 4:])
            
            # Lower low in price, higher low in RSI, current RSI emerging from oversold territory
            if recent_low < prior_trough_price and recent_rsi > (prior_trough_rsi + 2.0) and curr_rsi < 44.0:
                # Structural dynamic stop loss based on recent swing low
                price_risk_bps = max(100.0, min(300.0, ((curr_close - recent_low) / curr_close) * 10000.0 + 30.0))
                tp_bps = price_risk_bps * 1.8
                self.entry_bar = ctx.bar_index
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=price_risk_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bullish_rsi_divergence",
                        "rsi": curr_rsi,
                        "prior_trough_rsi": prior_trough_rsi,
                        "recent_rsi": recent_rsi,
                        "price": curr_close,
                        "risk_bps": price_risk_bps
                    }
                )

        # Bearish Divergence: Price made higher high, but RSI formed lower peak
        window_highs = highs[-self.lookback:]
        max_high_idx = window_highs.index(max(window_highs))
        if 4 <= max_high_idx <= self.lookback - 6:
            prior_peak_price = window_highs[max_high_idx]
            prior_peak_rsi = window_rsis[max_high_idx]
            recent_high = max(window_highs[max_high_idx + 4:])
            recent_rsi = max(window_rsis[max_high_idx + 4:])

            if recent_high > prior_peak_price and recent_rsi < (prior_peak_rsi - 2.0) and curr_rsi > 56.0:
                price_risk_bps = max(100.0, min(300.0, ((recent_high - curr_close) / curr_close) * 10000.0 + 30.0))
                tp_bps = price_risk_bps * 1.8
                self.entry_bar = ctx.bar_index
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=price_risk_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "bearish_rsi_divergence",
                        "rsi": curr_rsi,
                        "prior_peak_rsi": prior_peak_rsi,
                        "recent_rsi": recent_rsi,
                        "price": curr_close,
                        "risk_bps": price_risk_bps
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index