from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthScalpPullback(Strategy):
    METADATA = {
        "name": "EthScalpPullback",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 175.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 60,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 9
        self.trend_period = 30
        self.rsi_period = 9
        self.cooldown_bars = 75
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 9) -> Optional[float]:
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 20)
        highs = ctx.highs(self.trend_period + 20)
        lows = ctx.lows(self.trend_period + 20)
        opens = ctx.opens(self.trend_period + 20)

        if len(closes) < self.trend_period + 10:
            return None

        fast_ema = self._ema(closes, self.fast_period)
        trend_ema = self._ema(closes, self.trend_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or trend_ema is None or rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_conf = ctx.market.get("trend_regime_confidence", 0.0)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Strict crisis and volatility filter to protect against whipsaws
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL") or crisis_score > 0.45:
            return None

        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar

        # Position Management: Early dynamic exits on momentum exhaustion or trend break
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if fast_ema < trend_ema or rsi >= 75.0 or current_close < trend_ema:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_exhaustion_or_trend_loss",
                            "rsi": round(rsi, 2),
                            "fast_ema": round(fast_ema, 2),
                            "trend_ema": round(trend_ema, 2),
                            "close": round(current_close, 2),
                        },
                    )
            elif direction == "short":
                if fast_ema > trend_ema or rsi <= 25.0 or current_close > trend_ema:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_exhaustion_or_trend_loss",
                            "rsi": round(rsi, 2),
                            "fast_ema": round(fast_ema, 2),
                            "trend_ema": round(trend_ema, 2),
                            "close": round(current_close, 2),
                        },
                    )
            return None

        # Hard multi-bar cooldown guard to eliminate overtrading and friction bleed
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Trend strength separation (at least 0.15% difference between fast and slow EMA)
        ema_separation = (fast_ema - trend_ema) / trend_ema

        # LONG SETUP:
        # 1. Distinct macro & micro uptrend
        # 2. Strong pullback touch below/at fast EMA with decisive bullish close above it
        # 3. Previous bar was a pullback candle (close <= open or low < fast_ema)
        # 4. RSI reset into healthy 40-52 pullback zone
        prev_close = closes[-2]
        prev_open = opens[-2]
        
        long_trend = ema_separation > 0.0015 and trend_regime == "bull" and trend_conf >= 0.35
        long_pullback = (
            current_low <= fast_ema
            and current_close > fast_ema
            and current_close > current_open
            and (prev_close <= prev_open or lows[-2] <= fast_ema)
        )
        long_rsi_ok = 40.0 <= rsi <= 52.0
        long_funding_ok = funding < 0.0003

        if long_trend and long_pullback and long_rsi_ok and long_funding_ok:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=95.0,
                take_profit_bps=170.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "uptrend_ema_pullback_reclaim_confirmed",
                    "fast_ema": round(fast_ema, 2),
                    "trend_ema": round(trend_ema, 2),
                    "rsi": round(rsi, 2),
                    "close": round(current_close, 2),
                    "ema_separation_bps": round(ema_separation * 10000, 1),
                },
            )

        # SHORT SETUP:
        # 1. Distinct macro & micro downtrend
        # 2. Strong pullback touch above/at fast EMA with decisive bearish close below it
        # 3. Previous bar was a pullback candle (close >= open or high > fast_ema)
        # 4. RSI reset into healthy 48-60 pullback zone
        short_trend = ema_separation < -0.0015 and trend_regime == "bear" and trend_conf >= 0.35
        short_pullback = (
            current_high >= fast_ema
            and current_close < fast_ema
            and current_close < current_open
            and (prev_close >= prev_open or highs[-2] >= fast_ema)
        )
        short_rsi_ok = 48.0 <= rsi <= 60.0
        short_funding_ok = funding > -0.0003

        if short_trend and short_pullback and short_rsi_ok and short_funding_ok:
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=95.0,
                take_profit_bps=170.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "downtrend_ema_pullback_reject_confirmed",
                    "fast_ema": round(fast_ema, 2),
                    "trend_ema": round(trend_ema, 2),
                    "rsi": round(rsi, 2),
                    "close": round(current_close, 2),
                    "ema_separation_bps": round(ema_separation * 10000, 1),
                },
            )

        return None