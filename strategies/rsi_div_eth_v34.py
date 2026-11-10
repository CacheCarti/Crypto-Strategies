from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.last_exit_bar = -100
        self.cooldown_bars = 6
        self.entry_bar = -100

    def _calc_rsi_series(self, closes, period):
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
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(needed)
        if len(closes) < needed:
            return None

        highs = ctx.highs(needed)
        lows = ctx.lows(needed)

        rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback + 2:
            return None

        curr_rsi = rsi_vals[-1]
        curr_price = closes[-1]

        # Manage existing position exits
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction") if pos else None
            bars_held = ctx.bar_index - self.entry_bar

            if direction == "long":
                if curr_rsi >= 58.0 or bars_held >= 18:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_rsi_normalized_or_time", "rsi": curr_rsi, "bars_held": bars_held})
            elif direction == "short":
                if curr_rsi <= 42.0 or bars_held >= 18:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_rsi_normalized_or_time", "rsi": curr_rsi, "bars_held": bars_held})
            return None

        # Mandatory post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Volatility check via ATR to avoid dead regimes
        atr = self._atr(highs, lows, closes, 14)
        if atr < (curr_price * 0.003):
            return None

        # Crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Cross-asset filter: BTC 24h trend confirmation
        btc_return = ctx.features.get("btc_return_pct", 0.0)

        # Detect pivots over the lookback window
        # Align prices and RSI: rsi_vals[-1] corresponds to closes[-1]
        p_window = closes[-self.lookback:]
        r_window = rsi_vals[-self.lookback:]

        # Find recent lowest low in price
        min_p = min(p_window)
        min_idx = p_window.index(min_p)  # 0 to lookback-1

        # Bullish Divergence setup:
        # Price is forming a fresh lower low near recent bars, but RSI forms a higher low
        bull_div = False
        if min_idx >= self.lookback - 3 and min_idx > 8:
            prior_min_p = min(p_window[:min_idx - 4])
            prior_idx = p_window[:min_idx - 4].index(prior_min_p)
            # Divergence: current low is lower than prior low, but current RSI is higher than prior RSI
            if min_p < prior_min_p and r_window[min_idx] > r_window[prior_idx] + 2.5:
                # Oversold check
                if r_window[min_idx] < 38.0 and curr_rsi > r_window[min_idx]:
                    bull_div = True

        # Bearish Divergence setup:
        # Price forms a higher high near recent bars, but RSI forms a lower high
        bear_div = False
        max_p = max(p_window)
        max_idx = p_window.index(max_p)

        if max_idx >= self.lookback - 3 and max_idx > 8:
            prior_max_p = max(p_window[:max_idx - 4])
            prior_idx = p_window[:max_idx - 4].index(prior_max_p)
            if max_p > prior_max_p and r_window[max_idx] < r_window[prior_idx] - 2.5:
                # Overbought check
                if r_window[max_idx] > 62.0 and curr_rsi < r_window[max_idx]:
                    bear_div = True

        # Cross-asset confirmation filter prevents fighting systemic directional sweeps
        if bull_div and btc_return > -4.5:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                metadata={
                    "reason": "bullish_rsi_div_cross_asset_ok",
                    "curr_rsi": curr_rsi,
                    "curr_price": curr_price,
                    "btc_return": btc_return,
                    "min_price": min_p,
                }
            )

        if bear_div and btc_return < 4.5:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.70,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                metadata={
                    "reason": "bearish_rsi_div_cross_asset_ok",
                    "curr_rsi": curr_rsi,
                    "curr_price": curr_price,
                    "btc_return": btc_return,
                    "max_price": max_p,
                }
            )

        return None