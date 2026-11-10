from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SentimentAdaptivePullback(Strategy):
    METADATA = {
        "name": "SentimentAdaptivePullback",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 55,
        "required_features": ["twitter_sentiment"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.sentiment_history = []
        self.max_sentiment_hist = 200

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Track rolling sentiment distribution to avoid hardcoded magic thresholds
        raw_sent = float(ctx.features.get("twitter_sentiment", 0.0))
        self.sentiment_history.append(raw_sent)
        if len(self.sentiment_history) > self.max_sentiment_hist:
            self.sentiment_history.pop(0)

        # Baseline mean & trailing reference
        sma_fast = self._sma(closes, 20)
        if sma_fast is None:
            return None

        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        curr_price = ctx.bar.close

        # Compute dynamic percentile rank for sentiment
        sorted_sent = sorted(self.sentiment_history)
        n = len(sorted_sent)
        rank = sum(1 for s in sorted_sent if s <= raw_sent) / max(n, 1)

        # Extreme gates: rank <= 0.25 (fearful) or >= 0.75 (euphoric)
        is_fear = rank <= 0.25 or raw_sent <= -0.25
        is_greed = rank >= 0.75 or raw_sent >= 0.25

        # Handle exits if in position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", curr_price) if ctx.position else curr_price

            # Exit on mean reversion (crossing SMA20) or sentiment normalization
            if pos_dir == "long":
                if curr_price >= sma_fast or rank >= 0.55:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_mean_reversion_or_sentiment_normalized",
                            "close": curr_price,
                            "sma": sma_fast,
                            "sentiment_rank": round(rank, 3),
                        }
                    )
            elif pos_dir == "short":
                if curr_price <= sma_fast or rank <= 0.45:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_mean_reversion_or_sentiment_normalized",
                            "close": curr_price,
                            "sma": sma_fast,
                            "sentiment_rank": round(rank, 3),
                        }
                    )
            return None

        # Enforce cooldown after prior trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Price triggers: Pullback from recent extreme (approx 3% to 6%)
        # Long entry: Fearful sentiment + pullback from 48-bar high, holding support above recent low
        long_pullback_pct = (recent_high - curr_price) / recent_high
        short_rally_pct = (curr_price - recent_low) / recent_low

        if is_fear and 0.028 <= long_pullback_pct <= 0.08:
            # Confirm price bounced from intraday low (bar didn't close at the absolute low)
            if curr_price > ctx.bar.low:
                conf = min(0.85, 0.50 + abs(raw_sent) * 0.4)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=450.0,
                    take_profit_bps=750.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "fearful_dip_pullback",
                        "pullback_pct": round(long_pullback_pct, 4),
                        "sentiment": round(raw_sent, 3),
                        "sentiment_rank": round(rank, 3),
                        "recent_high": recent_high,
                        "close": curr_price,
                    }
                )

        # Short entry: Euphoric sentiment + rally from 48-bar low
        if is_greed and 0.028 <= short_rally_pct <= 0.08:
            if curr_price < ctx.bar.high:
                conf = min(0.85, 0.50 + abs(raw_sent) * 0.4)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=450.0,
                    take_profit_bps=750.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "euphoric_rally_pullback",
                        "rally_pct": round(short_rally_pct, 4),
                        "sentiment": round(raw_sent, 3),
                        "sentiment_rank": round(rank, 3),
                        "recent_low": recent_low,
                        "close": curr_price,
                    }
                )

        return None