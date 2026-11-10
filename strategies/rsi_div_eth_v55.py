from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 35
        self.pivot_order = 3
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _calculate_rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 2:
            return None

        # Build RSI series for lookback window
        rsi_series = []
        for i in range(len(closes) - self.lookback, len(closes) + 1):
            sub_closes = closes[:i]
            val = self._calculate_rsi(sub_closes, self.rsi_period)
            if val is not None:
                rsi_series.append(val)

        if len(rsi_series) < self.lookback:
            return None

        recent_closes = closes[-len(rsi_series):]
        current_rsi = rsi_series[-1]

        # Manage open position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_entry_bar
            # Mean-reversion target reached or time stop
            if pos_dir == "long" and (current_rsi >= 58.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_long_exit", "rsi": current_rsi})
            elif pos_dir == "short" and (current_rsi <= 42.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_reversion_short_exit", "rsi": current_rsi})
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: skip MELTDOWN or extreme crisis
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime == "MELTDOWN" or crisis_score > 0.8:
            return None

        # Detect swing lows and highs (pivot order = 3)
        pivot_lows = []   # (index, price, rsi)
        pivot_highs = []  # (index, price, rsi)
        k = self.pivot_order

        for i in range(k, len(recent_closes) - k):
            # Check pivot low
            c_mid = recent_closes[i]
            if all(c_mid < recent_closes[j] for j in range(i - k, i + k + 1) if j != i):
                pivot_lows.append((i, c_mid, rsi_series[i]))
            # Check pivot high
            if all(c_mid > recent_closes[j] for j in range(i - k, i + k + 1) if j != i):
                pivot_highs.append((i, c_mid, rsi_series[i]))

        # Bullish divergence setup:
        # Price: lower low (latest pivot lower than previous pivot)
        # RSI: higher low (latest pivot RSI higher than previous pivot RSI)
        # Current RSI must be recovering from oversold condition (< 42)
        if len(pivot_lows) >= 2:
            p1, p2 = pivot_lows[-2], pivot_lows[-1]
            idx_dist = p2[0] - p1[0]
            bars_since_p2 = (len(recent_closes) - 1) - p2[0]

            if 5 <= idx_dist <= 22 and bars_since_p2 <= 4:
                price_p1, rsi_p1 = p1[1], p1[2]
                price_p2, rsi_p2 = p2[1], p2[2]

                price_drop_pct = (price_p1 - price_p2) / price_p1
                rsi_gain = rsi_p2 - rsi_p1

                if price_drop_pct > 0.005 and rsi_gain > 2.5 and rsi_p2 < 42.0 and current_rsi > rsi_p2:
                    self.last_entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=450.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_rsi": rsi_p1,
                            "p2_rsi": rsi_p2,
                            "price_drop_pct": price_drop_pct,
                        }
                    )

        # Bearish divergence setup:
        # Price: higher high
        # RSI: lower high
        # Current RSI must be turning down from overbought condition (> 58)
        if len(pivot_highs) >= 2:
            p1, p2 = pivot_highs[-2], pivot_highs[-1]
            idx_dist = p2[0] - p1[0]
            bars_since_p2 = (len(recent_closes) - 1) - p2[0]

            if 5 <= idx_dist <= 22 and bars_since_p2 <= 4:
                price_p1, rsi_p1 = p1[1], p1[2]
                price_p2, rsi_p2 = p2[1], p2[2]

                price_rise_pct = (price_p2 - price_p1) / price_p1
                rsi_drop = rsi_p1 - rsi_p2

                if price_rise_pct > 0.005 and rsi_drop > 2.5 and rsi_p2 > 58.0 and current_rsi < rsi_p2:
                    self.last_entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=280.0,
                        take_profit_bps=450.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_rsi": rsi_p1,
                            "p2_rsi": rsi_p2,
                            "price_rise_pct": price_rise_pct,
                        }
                    )

        return None