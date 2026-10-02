from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "ETH RSI Divergence Swing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 8
        self.last_signal_bar = -100
        self.entry_bar = 0

    def _compute_rsi_series(self, closes: List[float], period: int, count: int) -> List[float]:
        needed = period + count + 5
        if len(closes) < needed:
            return []
        
        slice_closes = closes[-needed:]
        gains = []
        losses = []
        for i in range(1, len(slice_closes)):
            diff = slice_closes[i] - slice_closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        
        if len(gains) < period:
            return []

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi_list = []
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_list.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_list.append(100.0 - (100.0 / (1.0 + rs)))
        
        return rsi_list[-count:]

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_signal_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 10)
        lows = ctx.lows(self.lookback)
        highs = ctx.highs(self.lookback)

        if len(closes) < self.lookback + self.rsi_period + 5 or len(lows) < self.lookback:
            return None

        rsi_series = self._compute_rsi_series(closes, self.rsi_period, self.lookback)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        prev_rsi = rsi_series[-2]
        current_close = ctx.bar.close

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            if direction == "long":
                if current_rsi >= 62.0:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "rsi_bull_target_reached",
                            "rsi": round(current_rsi, 2),
                            "bars_held": bars_held,
                            "close": current_close
                        }
                    )
                if bars_held >= 16:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "time_stop_long_exit",
                            "rsi": round(current_rsi, 2),
                            "bars_held": bars_held,
                            "close": current_close
                        }
                    )
            elif direction == "short":
                if current_rsi <= 38.0:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "rsi_bear_target_reached",
                            "rsi": round(current_rsi, 2),
                            "bars_held": bars_held,
                            "close": current_close
                        }
                    )
                if bars_held >= 16:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "time_stop_short_exit",
                            "rsi": round(current_rsi, 2),
                            "bars_held": bars_held,
                            "close": current_close
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Avoid entries in crisis/meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70:
            return None

        # Pivot window split: older window [0..17] vs recent window [18..29]
        past_lows = lows[:18]
        recent_lows = lows[18:]
        past_highs = highs[:18]
        recent_highs = highs[18:]

        past_rsi = rsi_series[:18]
        recent_rsi = rsi_series[18:]

        past_min_price = min(past_lows)
        past_min_idx = past_lows.index(past_min_price)
        past_min_rsi = past_rsi[past_min_idx]

        recent_min_price = min(recent_lows)
        recent_min_idx = recent_lows.index(recent_min_price)
        recent_min_rsi = recent_rsi[recent_min_idx]

        past_max_price = max(past_highs)
        past_max_idx = past_highs.index(past_max_price)
        past_max_rsi = past_rsi[past_max_idx]

        recent_max_price = max(recent_highs)
        recent_max_idx = recent_highs.index(recent_max_price)
        recent_max_rsi = recent_rsi[recent_max_idx]

        # 1. Regular Bullish Divergence:
        # Price makes Lower Low, RSI makes Higher Low, current RSI turning up from oversold/neutral territory
        bullish_div = (
            recent_min_price < past_min_price * 0.998
            and recent_min_rsi > past_min_rsi + 2.5
            and current_rsi < 46.0
            and current_rsi > prev_rsi
            and recent_min_idx >= len(recent_lows) - 4
        )

        if bullish_div:
            self.last_signal_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "rsi": round(current_rsi, 2),
                    "past_min_price": round(past_min_price, 2),
                    "recent_min_price": round(recent_min_price, 2),
                    "past_min_rsi": round(past_min_rsi, 2),
                    "recent_min_rsi": round(recent_min_rsi, 2),
                    "crisis_score": round(crisis_score, 2),
                }
            )

        # 2. Regular Bearish Divergence:
        # Price makes Higher High, RSI makes Lower High, current RSI turning down from overbought/neutral territory
        bearish_div = (
            recent_max_price > past_max_price * 1.002
            and recent_max_rsi < past_max_rsi - 2.5
            and current_rsi > 54.0
            and current_rsi < prev_rsi
            and recent_max_idx >= len(recent_highs) - 4
        )

        if bearish_div:
            self.last_signal_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "rsi": round(current_rsi, 2),
                    "past_max_price": round(past_max_price, 2),
                    "recent_max_price": round(recent_max_price, 2),
                    "past_max_rsi": round(past_max_rsi, 2),
                    "recent_max_rsi": round(recent_max_rsi, 2),
                    "crisis_score": round(crisis_score, 2),
                }
            )

        return None