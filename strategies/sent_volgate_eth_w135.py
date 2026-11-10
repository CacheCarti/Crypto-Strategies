from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentVolConfirmation(Strategy):
    METADATA = {
        "name": "Sentiment Vol Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 12
        self.slow_ema_period = 26
        self.rsi_period = 14
        self.vol_sma_period = 20
        self.entry_cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.entry_cooldown_bars - 1 # Initialize to allow immediate trade

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
            return 100.0 if avg_gain > 0 else 50.0 # Handle flat market
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _volatility(self, closes, period):
        if len(closes) < period:
            return None
        log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return self._stddev(log_returns, period) * math.sqrt(252 * 24) # Annualized hourly volatility

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_trade_bar + self.entry_cooldown_bars:
            return None

        current_close = ctx.bar.close

        # Indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        
        # Realized volatility (using log returns standard deviation)
        realized_vol = self._volatility(closes, self.vol_sma_period)
        avg_vol = self._sma([self._volatility(closes[:i+1], self.vol_sma_period) for i in range(self.vol_sma_period - 1, len(closes))], self.vol_sma_period)


        if any(x is None for x in [fast_ema, slow_ema, rsi, realized_vol, avg_vol]):
            return None

        # Sentiment from Fear & Greed Index
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Volatility Confirmation: Significant deviation from average volatility indicates panic/euphoria
        vol_deviation_factor = 1.5 # How much higher vol needs to be than average

        # Long conditions
        long_condition_1 = rsi < 30 # Oversold
        long_condition_2 = fast_ema > slow_ema # Bullish trend
        long_condition_3 = fear_greed_index < 30 # Extreme Fear
        long_condition_4 = realized_vol > avg_vol * vol_deviation_factor # Panic/high volatility

        # Short conditions
        short_condition_1 = rsi > 70 # Overbought
        short_condition_2 = fast_ema < slow_ema # Bearish trend
        short_condition_3 = fear_greed_index > 70 # Extreme Greed
        short_condition_4 = realized_vol > avg_vol * vol_deviation_factor # Euphoria/high volatility

        if ctx.has_position():
            # If already in a position, we can look for exit signals or just hold
            # For this strategy, we'll let the SL/TP handle exits, or hold until reversal
            return ctx.signal("hold")

        # Entry Logic
        if long_condition_1 and long_condition_2 and long_condition_3 and long_condition_4:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fear_vol_confirmed_oversold_long",
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "fear_greed_index": fear_greed_index,
                    "realized_vol": realized_vol,
                    "avg_vol": avg_vol,
                    "current_price": current_close
                }
            )

        if short_condition_1 and short_condition_2 and short_condition_3 and short_condition_4:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "greed_vol_confirmed_overbought_short",
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "fear_greed_index": fear_greed_index,
                    "realized_vol": realized_vol,
                    "avg_vol": avg_vol,
                    "current_price": current_close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Use this for logging or adaptive sizing
        pass