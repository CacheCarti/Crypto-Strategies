from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class TripleEdgeConfluenceLong(Strategy):
    METADATA = {
        "name": "TripleEdgeConfluenceLong",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 28800,  # 8 hours hold horizon for swing reversion
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.atr_period = 14
        self.min_cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rsi_period + 5)
        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)

        if len(closes) < self.rsi_period + 1 or len(highs) < self.atr_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        if rsi is None or atr is None:
            return None

        current_price = ctx.bar.close
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Handle active position exit logic
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Structural/volatility exit or normalization of any of the 3 legs
            rsi_normalized = rsi >= 52.0
            sentiment_normalized = fear_greed >= 45.0
            funding_normalized = funding_rate > 0.0001
            timed_out = bars_held >= 18  # 18 hours max hold

            if rsi_normalized or sentiment_normalized or funding_normalized or timed_out:
                reason = "rsi_normalized" if rsi_normalized else (
                    "sentiment_normalized" if sentiment_normalized else (
                        "funding_normalized" if funding_normalized else "timed_out"
                    )
                )
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": f"exit_{reason}",
                        "rsi": round(rsi, 2),
                        "fear_greed": round(fear_greed, 2),
                        "funding_rate": funding_rate,
                        "bars_held": bars_held,
                        "price": current_price
                    }
                )
            return None

        # Check cooldown to prevent excessive trading and friction bleed
        if (ctx.bar_index - self.last_exit_bar) < self.min_cooldown_bars:
            return None

        # Triple edge confluence entry setup:
        # 1. Negative funding (crowded shorting)
        # 2. Crypto sentiment in fear (< 38 to ensure achievable sample size)
        # 3. Oversold price action on hourly timeframe (RSI < 36)
        edge_funding = funding_rate < 0.0
        edge_sentiment = fear_greed < 38.0
        edge_rsi = rsi < 36.0

        if edge_funding and edge_sentiment and edge_rsi:
            # Volatility-based adaptive stop and target
            atr_bps = (atr / current_price) * 10000.0
            stop_bps = max(350.0, min(650.0, atr_bps * 2.2))
            tp_bps = max(450.0, min(900.0, atr_bps * 3.5))

            # Confidence scales with the depth of the discount & fear
            conf = 0.65
            if rsi < 28.0:
                conf += 0.15
            if fear_greed < 25.0:
                conf += 0.10
            conf = min(0.95, conf)

            self.entry_bar = ctx.bar_index
            self.entry_price = current_price

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "triple_edge_confluence_oversold_short_squeeze",
                    "rsi": round(rsi, 2),
                    "fear_greed": round(fear_greed, 2),
                    "funding_rate": funding_rate,
                    "atr_bps": round(atr_bps, 1),
                    "price": current_price
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index