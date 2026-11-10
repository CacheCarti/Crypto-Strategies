from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_series = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0.0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period + 2:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]

        # Manage open position exits via RSI normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_rsi >= 58.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_long", "rsi": round(current_rsi, 2)}
                )
            elif pos_dir == "short" and current_rsi <= 42.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_short", "rsi": round(current_rsi, 2)}
                )
            return None

        # Respect cooldown between trades to reduce friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Exclude extreme market crisis to prevent catching falling knives
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Focus window for pivot detection
        win_size = min(self.lookback, len(rsi_series))
        w_lows = lows[-win_size:]
        w_highs = highs[-win_size:]
        w_rsi = rsi_series[-win_size:]

        # Detect swing low pivots: pivot if lower than immediate neighbors
        low_pivots = []
        for i in range(2, win_size - 1):
            if w_lows[i] <= w_lows[i - 1] and w_lows[i] <= w_lows[i - 2] and w_lows[i] <= w_lows[i + 1]:
                low_pivots.append((i, w_lows[i], w_rsi[i]))

        # Bullish Divergence check (only trigger when current RSI is still oversold/recovering)
        if len(low_pivots) >= 2 and current_rsi < 45.0:
            p1, p2 = low_pivots[-2], low_pivots[-1]
            # p2 is more recent than p1
            if p2[0] - p1[0] >= 4 and (win_size - 1 - p2[0]) <= 3:
                # Lower low in price, higher low in RSI
                if p2[1] < p1[1] and p2[2] > p1[2] and p1[2] <= 36.0:
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "p1_rsi": round(p1[2], 2),
                            "p2_rsi": round(p2[2], 2),
                            "p1_price": round(p1[1], 2),
                            "p2_price": round(p2[1], 2)
                        }
                    )

        # Detect swing high pivots
        high_pivots = []
        for i in range(2, win_size - 1):
            if w_highs[i] >= w_highs[i - 1] and w_highs[i] >= w_highs[i - 2] and w_highs[i] >= w_highs[i + 1]:
                high_pivots.append((i, w_highs[i], w_rsi[i]))

        # Bearish Divergence check (only trigger when current RSI is still elevated)
        if len(high_pivots) >= 2 and current_rsi > 55.0:
            p1, p2 = high_pivots[-2], high_pivots[-1]
            if p2[0] - p1[0] >= 4 and (win_size - 1 - p2[0]) <= 3:
                # Higher high in price, lower high in RSI
                if p2[1] > p1[1] and p2[2] < p1[2] and p1[2] >= 64.0:
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "p1_rsi": round(p1[2], 2),
                            "p2_rsi": round(p2[2], 2),
                            "p1_price": round(p1[1], 2),
                            "p2_price": round(p2[1], 2)
                        }
                    )

        return None