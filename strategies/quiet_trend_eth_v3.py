from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class CalmTrendFollower(Strategy):
    METADATA = {
        "name": "CalmTrendFollower",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aiming for 1-hour bar holds
        "warmup_bars": 100, # Longest period for volatility median
        "required_features": [], # No specific features needed beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.rsi_period = 14
        self.volatility_period = 30 # For ATR
        self.volatility_median_period = 60 # For comparing current ATR to its median
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trading

        self.last_regime = ctx.market.get("regime", "NORMAL") # Track regime for transitions

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_price = ctx.bar.close

        # Volatility check
        current_atr = self._atr(highs, lows, closes, self.volatility_period)
        if current_atr is None:
            return None

        # Get historical ATRs to find median
        # This is a bit computationally heavy, but should be acceptable for 1h bars
        # and limited history. For very long histories, pre-calculate.
        past_atrs = []
        for i in range(self.volatility_period, len(closes)):
            atr = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.volatility_period)
            if atr is not None:
                past_atrs.append(atr)

        if len(past_atrs) < self.volatility_median_period:
            return None

        recent_atrs = past_atrs[-self.volatility_median_period:]
        median_atr = statistics.median(recent_atrs)

        # Regime filter: only trade if volatility is below its median
        # and the market regime is not volatile/crisis.
        is_calm_volatility = current_atr < median_atr * 0.95 # 5% below median to be "calm"
        current_market_regime = ctx.market.get("regime", "NORMAL")
        is_calm_market = current_market_regime in ["NORMAL", "CALM"]

        # Track regime transition for exiting positions
        # If a position is open and regime changes to volatile, exit
        if ctx.has_position():
            if not is_calm_market and self.last_regime in ["NORMAL", "CALM"]:
                self.last_regime = current_market_regime
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "volatility_regime_change_exit", "current_regime": current_market_regime, "previous_regime": self.last_regime})
            self.last_regime = current_market_regime # Update for next bar

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Trend check
        is_bull_trend = fast_ema > slow_ema and closes[-1] > fast_ema
        is_bear_trend = fast_ema < slow_ema and closes[-1] < fast_ema

        # Entry logic
        if not ctx.has_position():
            if is_calm_volatility and is_calm_market:
                # Long entry: Bull trend, RSI not overbought (below 65)
                if is_bull_trend and rsi < 65:
                    self.last_trade_bar = ctx.bar_index
                    self.last_regime = current_market_regime
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "calm_bull_trend_entry",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "rsi": rsi,
                            "current_atr": current_atr,
                            "median_atr": median_atr,
                            "market_regime": current_market_regime
                        }
                    )
                # Short entry: Bear trend, RSI not oversold (above 35)
                elif is_bear_trend and rsi > 35:
                    self.last_trade_bar = ctx.bar_index
                    self.last_regime = current_market_regime
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "calm_bear_trend_entry",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "rsi": rsi,
                            "current_atr": current_atr,
                            "median_atr": median_atr,
                            "market_regime": current_market_regime
                        }
                    )

        # If holding a position, check for exit conditions (beyond SL/TP)
        if ctx.has_position():
            # Exit if volatility expands or market becomes volatile/crisis
            if not is_calm_volatility or not is_calm_market:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "volatility_expansion_or_regime_change_exit",
                        "current_atr": current_atr,
                        "median_atr": median_atr,
                        "market_regime": current_market_regime,
                        "is_calm_volatility": is_calm_volatility,
                        "is_calm_market": is_calm_market
                    }
                )

            # Additional exit conditions based on trend reversal (EMA cross)
            if ctx.position_direction() == "long":
                if fast_ema < slow_ema: # Fast EMA crosses below slow EMA
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_trend_reversal_ema_cross_exit",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": current_price
                        }
                    )
            elif ctx.position_direction() == "short":
                if fast_ema > slow_ema: # Fast EMA crosses above slow EMA
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_trend_reversal_ema_cross_exit",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_price": current_price
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar on close, ensuring cooldown logic applies after any exit
        self.last_trade_bar = ctx.bar_index
        # No adaptive sizing for this strategy, just logging
        pass