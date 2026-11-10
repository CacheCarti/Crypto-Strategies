from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateDivergence(Strategy):
    METADATA = {
        "name": "Funding Rate Divergence BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour average hold
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt", "funding_rate_ethusdt", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_diff_period = 20
        self.funding_zscore_threshold = 1.5
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.ema_fast_period = 10
        self.ema_slow_period = 20
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Store previous funding diff to detect transitions
        self.prev_funding_zscore = 0.0
        self.prev_funding_diff_mean = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def _zscore(self, values, period):
        if len(values) < period:
            return None
        mean = sum(values[-period:]) / period
        std = self._stddev(values, period)
        if std is None or std == 0:
            return 0.0
        return (values[-1] - mean) / std

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
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1) # Need one more for RSI diff
        if len(closes) < self.warmup_bars + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get funding rates
        btc_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        eth_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        sol_funding = ctx.features.get("funding_rate_solusdt", 0.0)

        # Calculate average altcoin funding
        alt_funding_avg = (eth_funding + sol_funding) / 2.0

        # Calculate funding rate difference (BTC vs Alts)
        funding_diff = btc_funding - alt_funding_avg

        # Maintain a history of funding differences for z-score calculation
        # Store a dummy value for older bars to ensure enough data for z-score
        funding_diff_history = [0.0] * (self.warmup_bars - len(ctx.closes(self.warmup_bars))) + \
                               [ctx.features.get("funding_rate_btcusdt", 0.0) - (ctx.features.get("funding_rate_ethusdt", 0.0) + ctx.features.get("funding_rate_solusdt", 0.0))/2.0 for _ in range(self.warmup_bars - self.funding_diff_period, len(ctx.closes(self.warmup_bars)))]

        # This is a simplification; a proper history needs to be maintained.
        # For this example, we'll use a rolling window of recent values.
        # In a real scenario, you'd store funding diffs in `self` and update on each bar.
        # For simplicity, we'll just use the current bar's values for a rolling window.
        
        # Fake a rolling history for demonstration purposes, assuming features are available for previous bars
        # This is not ideal for true Z-score but fits the constraint of not storing full history on self
        # A more robust solution would involve storing funding_diff values in a deque in `self`
        
        # To simplify and ensure backtestability within constraints:
        # We will use the current funding diff and compare it to a recent average.
        # This approach avoids needing to store a long history in `self`.
        
        # Calculate a simple moving average of funding diffs for the last `funding_diff_period` bars
        # This is a proxy for the mean of the distribution for z-score
        
        # We need to ensure we have enough historical funding rates.
        # Since we only get current bar features, we must approximate.
        # A better approach would be to store a list in self.
        
        # For the sake of demonstration and respecting output size/performance,
        # we'll use a very simplified "z-score" proxy based on a short recent window.
        # This is a compromise given the constraints.
        
        # Actual implementation requires storing historical values of funding_diff in `self`
        # For this example, we'll assume a simplified z-score check.
        
        # Simplified Z-score proxy: compare current funding_diff to a recent EMA of funding_diffs
        # This requires storing the EMA on self.
        if not hasattr(self, 'funding_diff_ema'):
            self.funding_diff_ema = funding_diff
            self.funding_diff_std_dev = 0.0 # Placeholder
            self.funding_diff_history_for_std = [funding_diff] * self.funding_diff_period # For std dev
        else:
            self.funding_diff_ema = self.funding_diff_ema * 0.9 + funding_diff * 0.1 # Simple EMA update
            self.funding_diff_history_for_std.append(funding_diff)
            if len(self.funding_diff_history_for_std) > self.funding_diff_period:
                self.funding_diff_history_for_std.pop(0)
            self.funding_diff_std_dev = self._stddev(self.funding_diff_history_for_std, self.funding_diff_period)

        current_funding_zscore = 0.0
        if self.funding_diff_std_dev > 0:
            current_funding_zscore = (funding_diff - self.funding_diff_ema) / self.funding_diff_std_dev

        # Price action indicators
        rsi = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        current_price = closes[-1]

        # Entry logic
        if not ctx.has_position():
            # Long condition: BTC funding significantly lower than alts (oversold BTC relative positioning)
            # AND price action shows bullish signs (RSI oversold, fast EMA above slow EMA)
            if current_funding_zscore < -self.funding_zscore_threshold and \
               self.prev_funding_zscore >= -self.funding_zscore_threshold and \
               rsi < self.rsi_oversold and \
               ema_fast > ema_slow:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "btc_funding_divergence_long",
                        "btc_funding": btc_funding,
                        "alt_funding_avg": alt_funding_avg,
                        "funding_diff": funding_diff,
                        "funding_zscore": current_funding_zscore,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": current_price
                    }
                )

            # Short condition: BTC funding significantly higher than alts (overbought BTC relative positioning)
            # AND price action shows bearish signs (RSI overbought, fast EMA below slow EMA)
            elif current_funding_zscore > self.funding_zscore_threshold and \
                 self.prev_funding_zscore <= self.funding_zscore_threshold and \
                 rsi > self.rsi_overbought and \
                 ema_fast < ema_slow:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "btc_funding_divergence_short",
                        "btc_funding": btc_funding,
                        "alt_funding_avg": alt_funding_avg,
                        "funding_diff": funding_diff,
                        "funding_zscore": current_funding_zscore,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "current_price": current_price
                    }
                )

        # Update previous funding z-score for transition detection
        self.prev_funding_zscore = current_funding_zscore
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for this strategy on close, but can be added here.
        pass