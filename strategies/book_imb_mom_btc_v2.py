from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcOrderFlowMomentum(Strategy):
    METADATA = {
        "name": "BtcOrderFlowMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.breakout_period = 24
        self.exit_period = 10
        self.rsi_period = 14
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.imbalance_thresh = 0.08

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = max(self.breakout_period + 5, self.rsi_period + 5)
        closes = ctx.closes(needed)
        highs = ctx.highs(needed)
        lows = ctx.lows(needed)

        if len(closes) < needed:
            return None

        # Filter out extreme crisis regimes
        if ctx.regime == "crisis" or ctx.market.get("crisis_score", 0.0) > 0.6:
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_regime_exit", "crisis_score": ctx.market.get("crisis_score", 0.0)}
                )
            return None

        current_close = ctx.bar.close
        book_imb = ctx.features.get("book_imbalance_btcusdt", 0.0)
        rsi = self._rsi(closes, self.rsi_period)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Less twitchy momentum stall exit to prevent premature churn
            if pos_dir == "long":
                exit_low = min(lows[-self.exit_period - 1:-1])
                if current_close < exit_low or rsi < 38.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_stall",
                            "rsi": round(rsi, 2),
                            "exit_low": round(exit_low, 2),
                            "close": round(current_close, 2),
                            "book_imbalance": round(book_imb, 4),
                        }
                    )
            elif pos_dir == "short":
                exit_high = max(highs[-self.exit_period - 1:-1])
                if current_close > exit_high or rsi > 62.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_stall",
                            "rsi": round(rsi, 2),
                            "exit_high": round(exit_high, 2),
                            "close": round(current_close, 2),
                            "book_imbalance": round(book_imb, 4),
                        }
                    )
            return None

        # Hard multi-bar cooldown guard after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Lookback channel for multi-period breakout (excluding current bar)
        prior_highs = highs[-self.breakout_period - 1:-1]
        prior_lows = lows[-self.breakout_period - 1:-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        # Long breakout setup: strong breakout + significant positive book imbalance + bullish RSI
        if current_close > channel_high and book_imb >= self.imbalance_thresh and rsi >= 56.0:
            conf = min(0.9, max(0.55, 0.6 + book_imb * 0.4))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=640.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "bullish_breakout_orderflow_confirmed",
                    "channel_high": round(channel_high, 2),
                    "close": round(current_close, 2),
                    "book_imbalance": round(book_imb, 4),
                    "rsi": round(rsi, 2),
                }
            )

        # Short breakdown setup: strong breakdown + significant negative book imbalance + bearish RSI
        if current_close < channel_low and book_imb <= -self.imbalance_thresh and rsi <= 44.0:
            conf = min(0.9, max(0.55, 0.6 + abs(book_imb) * 0.4))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=320.0,
                take_profit_bps=640.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "bearish_breakdown_orderflow_confirmed",
                    "channel_low": round(channel_low, 2),
                    "close": round(current_close, 2),
                    "book_imbalance": round(book_imb, 4),
                    "rsi": round(rsi, 2),
                }
            )

        return None