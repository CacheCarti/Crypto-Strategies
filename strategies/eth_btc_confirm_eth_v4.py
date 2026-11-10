from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcLeaderConfirm(Strategy):
    METADATA = {
        "name": "ETH-BTC Leader Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.slow_period = 26
        self.signal_period = 9
        self.rsi_period = 14
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _macd(self, closes, fast, slow):
        if len(closes) < slow:
            return None, None
        fast_ema = self._ema(closes, fast)
        slow_ema = self._ema(closes, slow)
        if fast_ema is None or slow_ema is None:
            return None, None
        return fast_ema - slow_ema, fast_ema, slow_ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero, neutral if no movement
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + self.signal_period + self.rsi_period + 1)
        if len(closes) < self.slow_period + self.signal_period + self.rsi_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        eth_macd_line, eth_fast_ema, eth_slow_ema = self._macd(closes, self.fast_period, self.slow_period)
        if eth_macd_line is None or eth_fast_ema is None or eth_slow_ema is None:
            return None

        # Calculate MACD signal line and histogram (approximate for efficiency)
        macd_values = []
        for i in range(self.slow_period, len(closes)):
            f_ema = self._ema(closes[:i+1], self.fast_period)
            s_ema = self._ema(closes[:i+1], self.slow_period)
            if f_ema is not None and s_ema is not None:
                macd_values.append(f_ema - s_ema)

        if len(macd_values) < self.signal_period:
            return None

        signal_line = self._ema(macd_values, self.signal_period)
        if signal_line is None:
            return None

        histogram = eth_macd_line - signal_line
        eth_rsi = self._rsi(closes, self.rsi_period)
        if eth_rsi is None:
            return None

        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Volatility/Crisis filter: Stand down during high crisis
        if crisis_score > 0.3: # Adjust threshold based on backtest results
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "crisis_regime_exit", "crisis_score": crisis_score})
            return None

        # Entry logic
        if not ctx.has_position():
            # Long condition: ETH MACD bullish cross, ETH RSI not overbought, BTC confirming upwards momentum
            if histogram > 0 and macd_values[-2] < macd_values[-1] and eth_rsi < 70 and eth_return_pct > 0.5:
                # BTC confirmation: BTC also showing positive return
                if btc_return_pct > 0.1: # BTC has positive momentum
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "eth_macd_bull_cross_btc_confirm",
                            "eth_macd_line": eth_macd_line,
                            "eth_signal_line": signal_line,
                            "eth_histogram": histogram,
                            "eth_rsi": eth_rsi,
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "crisis_score": crisis_score,
                        }
                    )

            # Short condition: ETH MACD bearish cross, ETH RSI not oversold, BTC confirming downwards momentum
            elif histogram < 0 and macd_values[-2] > macd_values[-1] and eth_rsi > 30 and eth_return_pct < -0.5:
                # BTC confirmation: BTC also showing negative return
                if btc_return_pct < -0.1: # BTC has negative momentum
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "eth_macd_bear_cross_btc_confirm",
                            "eth_macd_line": eth_macd_line,
                            "eth_signal_line": signal_line,
                            "eth_histogram": histogram,
                            "eth_rsi": eth_rsi,
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "crisis_score": crisis_score,
                        }
                    )

        # Exit logic
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if MACD momentum exhausts or BTC turns negative
                if histogram < 0 or btc_return_pct < -0.1 or eth_rsi > 75: # RSI overbought also exit
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exit_macd_exhaustion_or_btc_loss_or_rsi_overbought",
                            "eth_macd_line": eth_macd_line,
                            "eth_signal_line": signal_line,
                            "eth_histogram": histogram,
                            "eth_rsi": eth_rsi,
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "crisis_score": crisis_score,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit short if MACD momentum exhausts or BTC turns positive
                if histogram > 0 or btc_return_pct > 0.1 or eth_rsi < 25: # RSI oversold also exit
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exit_macd_exhaustion_or_btc_loss_or_rsi_oversold",
                            "eth_macd_line": eth_macd_line,
                            "eth_signal_line": signal_line,
                            "eth_histogram": histogram,
                            "eth_rsi": eth_rsi,
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "crisis_score": crisis_score,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive sizing in this strategy, but could be.
        pass