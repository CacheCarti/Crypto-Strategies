from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.min_pivot_distance = 6

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[list]:
        if len(closes) < period + 1:
            return None
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

    def _find_pivot_lows(self, lows: list, rsis: list, window: int):
        pivots = []
        for i in range(2, window - 2):
            if lows[i] <= lows[i - 1] and lows[i] < lows[i - 2] and lows[i] <= lows[i + 1] and lows[i] < lows[i + 2]:
                pivots.append((i, lows[i], rsis[i]))
        return pivots

    def _find_pivot_highs(self, highs: list, rsis: list, window: int):
        pivots = []
        for i in range(2, window - 2):
            if highs[i] >= highs[i - 1] and highs[i] > highs[i - 2] and highs[i] >= highs[i + 1] and highs[i] > highs[i + 2]:
                pivots.append((i, highs[i], rsis[i]))
        return pivots

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_bars_needed = self.lookback + self.rsi_period + 5
        closes = ctx.closes(total_bars_needed)
        if len(closes) < total_bars_needed:
            return None

        highs = ctx.highs(total_bars_needed)
        lows = ctx.lows(total_bars_needed)

        rsi_vals = self._calc_rsi(closes, self.rsi_period)
        if not rsi_vals or len(rsi_vals) < self.lookback:
            return None

        current_rsi = rsi_vals[-1]
        current_close = closes[-1]

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_rsi >= 58.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_long", "rsi": current_rsi, "close": current_close}
                )
            elif pos_dir == "short" and current_rsi <= 42.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_short", "rsi": current_rsi, "close": current_close}
                )
            return None

        # Respect cooldown to limit overtrading and save friction
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Volatility / crisis filter: Avoid entering during extreme crisis dislocations
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset trend alignment confirmation via BTC 24h return
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Slice relevant lookback window for pivot search
        sub_lows = lows[-self.lookback:]
        sub_highs = highs[-self.lookback:]
        sub_rsi = rsi_vals[-self.lookback:]

        pivot_lows = self._find_pivot_lows(sub_lows, sub_rsi, self.lookback)
        pivot_highs = self._find_pivot_highs(sub_highs, sub_rsi, self.lookback)

        # Check Bullish Divergence (Price Lower Low, RSI Higher Low)
        if len(pivot_lows) >= 2:
            p1 = pivot_lows[-2]
            p2 = pivot_lows[-1]
            idx_diff = p2[0] - p1[0]
            bars_since_p2 = (self.lookback - 1) - p2[0]

            if idx_diff >= self.min_pivot_distance and bars_since_p2 <= 4:
                price_p1, rsi_p1 = p1[1], p1[2]
                price_p2, rsi_p2 = p2[1], p2[2]

                if price_p2 < price_p1 and rsi_p2 > (rsi_p1 + 1.5) and rsi_p2 < 42.0:
                    if btc_ret >= -4.5:
                        conf = min(0.85, 0.55 + max(0.0, (rsi_p2 - rsi_p1) * 0.02))
                        return ctx.signal(
                            "long",
                            confidence=conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_rsi_div_cross_confirmed",
                                "rsi": current_rsi,
                                "p1_price": price_p1,
                                "p2_price": price_p2,
                                "p1_rsi": rsi_p1,
                                "p2_rsi": rsi_p2,
                                "btc_ret": btc_ret
                            }
                        )

        # Check Bearish Divergence (Price Higher High, RSI Lower High)
        if len(pivot_highs) >= 2:
            p1 = pivot_highs[-2]
            p2 = pivot_highs[-1]
            idx_diff = p2[0] - p1[0]
            bars_since_p2 = (self.lookback - 1) - p2[0]

            if idx_diff >= self.min_pivot_distance and bars_since_p2 <= 4:
                price_p1, rsi_p1 = p1[1], p1[2]
                price_p2, rsi_p2 = p2[1], p2[2]

                if price_p2 > price_p1 and rsi_p2 < (rsi_p1 - 1.5) and rsi_p2 > 58.0:
                    if btc_ret <= 4.5:
                        conf = min(0.85, 0.55 + max(0.0, (rsi_p1 - rsi_p2) * 0.02))
                        return ctx.signal(
                            "short",
                            confidence=conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_rsi_div_cross_confirmed",
                                "rsi": current_rsi,
                                "p1_price": price_p1,
                                "p2_price": price_p2,
                                "p1_rsi": rsi_p1,
                                "p2_rsi": rsi_p2,
                                "btc_ret": btc_ret
                            }
                        )

        return None