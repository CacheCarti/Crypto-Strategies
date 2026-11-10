from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergencePivot(Strategy):
    METADATA = {
        "name": "EthRsiDivergencePivot",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_trade_bar = -999

    def _calculate_rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        
        gains, losses = [], []
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
        req_bars = self.lookback + self.rsi_period + 5
        closes = ctx.closes(req_bars)
        lows = ctx.lows(req_bars)
        highs = ctx.highs(req_bars)

        if len(closes) < req_bars:
            return None

        # Build RSI series aligned with price
        rsi_series = self._calculate_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]

        # Handle active position exit conditions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Mean reversion normalization exit
            if pos_dir == "long" and current_rsi >= 55.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_reversion_bull_exit", "rsi": round(current_rsi, 2)}
                )
            elif pos_dir == "short" and current_rsi <= 45.0:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_reversion_bear_exit", "rsi": round(current_rsi, 2)}
                )
            return None

        # Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime safety filter from market context
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Detect discrete pivot confirmation at bar -2 (1-bar confirmation lag)
        # Check pivot low at index -2
        idx = -2
        curr_price_low = lows[idx]
        curr_rsi_low = rsi_series[idx]

        is_pivot_low = (lows[idx] < lows[idx - 1]) and (lows[idx] < lows[idx + 1])
        is_pivot_high = (highs[idx] > highs[idx - 1]) and (highs[idx] > highs[idx + 1])

        # Strict event boundary: only trigger immediately upon pivot confirmation
        # Bullish Divergence: Price Lower Low, RSI Higher Low, confirmed bounce
        if is_pivot_low and curr_rsi_low < 38.0:
            # Look for an earlier pivot low within the lookback window
            search_start = max(0, len(rsi_series) - self.lookback)
            prev_low_price = None
            prev_low_rsi = None

            for i in range(len(rsi_series) - 5, search_start, -1):
                if (lows[i] < lows[i - 1]) and (lows[i] < lows[i + 1]):
                    prev_low_price = lows[i]
                    prev_low_rsi = rsi_series[i]
                    break

            if prev_low_price is not None and prev_low_rsi is not None:
                # Discrete divergence check
                if curr_price_low < prev_low_price and curr_rsi_low > prev_low_rsi:
                    # Confirmation filter: current bar is expanding upwards
                    if closes[-1] > closes[-2]:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=250.0,
                            take_profit_bps=400.0,
                            horizon_seconds=14400,
                            metadata={
                                "reason": "bullish_rsi_div_pivot",
                                "curr_low": round(curr_price_low, 2),
                                "prev_low": round(prev_low_price, 2),
                                "curr_rsi": round(curr_rsi_low, 2),
                                "prev_rsi": round(prev_low_rsi, 2)
                            }
                        )

        # Bearish Divergence: Price Higher High, RSI Lower High, confirmed rejection
        if is_pivot_high and curr_rsi_low > 62.0:
            curr_price_high = highs[idx]
            curr_rsi_high = rsi_series[idx]

            search_start = max(0, len(rsi_series) - self.lookback)
            prev_high_price = None
            prev_high_rsi = None

            for i in range(len(rsi_series) - 5, search_start, -1):
                if (highs[i] > highs[i - 1]) and (highs[i] > highs[i + 1]):
                    prev_high_price = highs[i]
                    prev_high_rsi = rsi_series[i]
                    break

            if prev_high_price is not None and prev_high_rsi is not None:
                if curr_price_high > prev_high_price and curr_rsi_high < prev_high_rsi:
                    # Confirmation filter: current bar is expanding downwards
                    if closes[-1] < closes[-2]:
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=250.0,
                            take_profit_bps=400.0,
                            horizon_seconds=14400,
                            metadata={
                                "reason": "bearish_rsi_div_pivot",
                                "curr_high": round(curr_price_high, 2),
                                "prev_high": round(prev_high_price, 2),
                                "curr_rsi": round(curr_rsi_high, 2),
                                "prev_rsi": round(prev_high_rsi, 2)
                            }
                        )

        return None