from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarrySwing(Strategy):
    METADATA = {
        "name": "BTC Funding Rate Carry Swing",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 55,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 21
        self.slow_ema_period = 55
        self.rsi_period = 14
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_entry_bar = -999

        # Funding rate thresholds (per 8h rate)
        self.negative_funding_thresh = 0.00002   # <= 0.002% indicates cheap/negative carry
        self.high_funding_thresh = 0.00025       # >= 0.025% indicates crowded/expensive longs

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.slow_ema_period + 5)
        if len(closes) < self.slow_ema_period:
            return None

        current_price = ctx.bar.close
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        trend_bullish = fast_ema > slow_ema and current_price > slow_ema
        trend_bearish = fast_ema < slow_ema and current_price < slow_ema

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar

        # --- Position Management & Exits ---
        if has_pos:
            if pos_dir == "long":
                # Exit if funding becomes excessively overheated with overbought momentum
                if (funding_rate > self.high_funding_thresh and rsi > 70.0) or (trend_bearish and rsi < 42.0):
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "carry_exhaustion_or_trend_break_long",
                            "funding_rate": funding_rate,
                            "rsi": rsi,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "price": current_price,
                        },
                    )
            elif pos_dir == "short":
                # Exit if funding collapses to deeply negative with oversold bounce
                if (funding_rate < -0.00005 and rsi < 30.0) or (trend_bullish and rsi > 58.0):
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "carry_exhaustion_or_trend_break_short",
                            "funding_rate": funding_rate,
                            "rsi": rsi,
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "price": current_price,
                        },
                    )
            return None

        # --- Cooldown Guard ---
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # --- Long Carry Entry ---
        # Favorable carry (low/negative funding) + Bullish structure + RSI not overbought
        long_carry_condition = funding_rate <= self.negative_funding_thresh
        long_momentum_condition = 40.0 <= rsi <= 64.0
        if long_carry_condition and trend_bullish and long_momentum_condition:
            self.last_entry_bar = ctx.bar_index
            conf = 0.65 + min(0.25, max(0.0, -funding_rate * 2000.0))
            return ctx.signal(
                "long",
                confidence=min(0.95, conf),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "cheap_funding_bullish_carry_entry",
                    "funding_rate": funding_rate,
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price": current_price,
                },
            )

        # --- Short Carry Fade Entry ---
        # Squeeze/Crowded long penalty (high funding) + Bearish structure + RSI not oversold
        short_carry_condition = funding_rate >= self.high_funding_thresh
        short_momentum_condition = 36.0 <= rsi <= 60.0
        if short_carry_condition and trend_bearish and short_momentum_condition:
            self.last_entry_bar = ctx.bar_index
            conf = 0.65 + min(0.25, max(0.0, (funding_rate - self.high_funding_thresh) * 1500.0))
            return ctx.signal(
                "short",
                confidence=min(0.95, conf),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "crowded_long_funding_fade_entry",
                    "funding_rate": funding_rate,
                    "rsi": rsi,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "price": current_price,
                },
            )

        return None