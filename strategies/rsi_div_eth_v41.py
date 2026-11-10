from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.pivot_window = 3
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.entry_bar = -999

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
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

    def _calc_rsi_series(self, closes: list, period: int = 14, length: int = 35) -> list:
        series = []
        for end_idx in range(len(closes) - length, len(closes) + 1):
            sub = closes[:end_idx]
            series.append(self._calc_rsi(sub, period))
        return series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 6)
        lows = ctx.lows(self.lookback + 6)
        highs = ctx.highs(self.lookback + 6)

        if len(closes) < self.lookback + self.rsi_period + 2:
            return None

        # Build recent RSI values
        needed_rsi_len = self.lookback + 2
        rsi_series = self._calc_rsi_series(closes, self.rsi_period, needed_rsi_len)
        current_rsi = rsi_series[-1]

        if current_rsi is None:
            return None

        # Manage active position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Normalized RSI dynamic take-profit / momentum exhaustion exit
            if pos_dir == "long" and current_rsi >= 60.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": current_rsi})
            elif pos_dir == "short" and current_rsi <= 40.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": current_rsi})
            
            # Explicit time stop (6 bars / 6 hours on 1h bars)
            if bars_held >= 6:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_bars_timeout", "bars_held": bars_held, "rsi": current_rsi})
            return None

        # Mandatory Cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Crisis filter from market context
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        pw = self.pivot_window
        recent_lows = lows[-(self.lookback):]
        recent_highs = highs[-(self.lookback):]
        recent_rsi = rsi_series[-(self.lookback):]

        # Scan for swing low pivots
        low_pivots = []
        for i in range(pw, len(recent_lows) - pw):
            window = recent_lows[i - pw : i + pw + 1]
            if recent_lows[i] == min(window) and recent_rsi[i] is not None:
                low_pivots.append((i, recent_lows[i], recent_rsi[i]))

        # Scan for swing high pivots
        high_pivots = []
        for i in range(pw, len(recent_highs) - pw):
            window = recent_highs[i - pw : i + pw + 1]
            if recent_highs[i] == max(window) and recent_rsi[i] is not None:
                high_pivots.append((i, recent_highs[i], recent_rsi[i]))

        # Bullish Divergence Check
        if len(low_pivots) >= 2:
            p1 = low_pivots[-2]
            p2 = low_pivots[-1]
            # Ensure p2 is recent (formed within last 8 bars)
            if (len(recent_lows) - 1 - p2[0]) <= 8:
                # Lower low in price, higher low in RSI
                if p2[1] < p1[1] and p2[2] > (p1[2] + 1.5) and p2[2] <= 45.0:
                    # Trigger entry if price forms an immediate confirmation upward tick
                    if closes[-1] > closes[-2]:
                        self.last_trade_bar = ctx.bar_index
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=250.0,
                            take_profit_bps=450.0,
                            horizon_seconds=21600,
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "prev_pivot_price": p1[1],
                                "curr_pivot_price": p2[1],
                                "prev_pivot_rsi": round(p1[2], 2),
                                "curr_pivot_rsi": round(p2[2], 2),
                                "current_rsi": round(current_rsi, 2)
                            }
                        )

        # Bearish Divergence Check
        if len(high_pivots) >= 2:
            p1 = high_pivots[-2]
            p2 = high_pivots[-1]
            # Ensure p2 is recent (formed within last 8 bars)
            if (len(recent_highs) - 1 - p2[0]) <= 8:
                # Higher high in price, lower high in RSI
                if p2[1] > p1[1] and p2[2] < (p1[2] - 1.5) and p2[2] >= 55.0:
                    # Trigger entry on downward confirmation tick
                    if closes[-1] < closes[-2]:
                        self.last_trade_bar = ctx.bar_index
                        self.entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=250.0,
                            take_profit_bps=450.0,
                            horizon_seconds=21600,
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "prev_pivot_price": p1[1],
                                "curr_pivot_price": p2[1],
                                "prev_pivot_rsi": round(p1[2], 2),
                                "curr_pivot_rsi": round(p2[2], 2),
                                "current_rsi": round(current_rsi, 2)
                            }
                        )

        return None