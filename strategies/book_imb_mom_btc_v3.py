from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcChannelMomentumFlow(Strategy):
    METADATA = {
        "name": "BTC Channel Momentum Flow",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 40,
        "required_features": ["book_imbalance_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.breakout_period = 24
        self.exit_ema_period = 14
        self.cooldown_bars = 10
        self.imbalance_thresh = 0.05
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / float(period)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.breakout_period + 10
        closes = ctx.closes(total_needed)
        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)
        volumes = ctx.volumes(total_needed)

        if len(closes) < total_needed:
            return None

        current_close = ctx.bar.close
        prev_close = closes[-2]
        exit_ema = self._ema(closes, self.exit_ema_period)
        vol_sma = self._sma(volumes, 20)
        book_imb = ctx.features.get("book_imbalance_btcusdt", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Handle active position exits (Momentum Stall on EMA cross)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if exit_ema is not None:
                if pos_dir == "long" and current_close < exit_ema and prev_close >= exit_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_momentum_stall_ema_cross",
                            "close": current_close,
                            "exit_ema": exit_ema,
                            "book_imbalance": book_imb,
                        },
                    )
                elif pos_dir == "short" and current_close > exit_ema and prev_close <= exit_ema:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_momentum_stall_ema_cross",
                            "close": current_close,
                            "exit_ema": exit_ema,
                            "book_imbalance": book_imb,
                        },
                    )
            return None

        # Hard Cooldown Guard after exit to avoid friction churn
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime protection
        if crisis_score > 0.5:
            return None

        # Reference prior 24 bars (excluding the current forming bar)
        prior_highs = highs[-(self.breakout_period + 1):-1]
        prior_lows = lows[-(self.breakout_period + 1):-1]
        channel_high = max(prior_highs)
        channel_low = min(prior_lows)

        # Previous bar channel boundary to detect fresh breakout cross
        prev_channel_high = max(highs[-(self.breakout_period + 2):-2])
        prev_channel_low = min(lows[-(self.breakout_period + 2):-2])

        # Volume confirmation filter
        vol_active = vol_sma is not None and ctx.bar.volume >= (vol_sma * 0.9)

        # Fresh Long Breakout: Close crosses above 24h high with confirmed buy flow
        if (
            current_close > channel_high
            and prev_close <= prev_channel_high
            and book_imb >= self.imbalance_thresh
            and vol_active
        ):
            confidence = min(0.9, 0.6 + max(0.0, book_imb) * 0.5)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fresh_channel_breakout_high_flow_confirmed",
                    "channel_high": channel_high,
                    "close": current_close,
                    "book_imbalance": book_imb,
                    "volume_ratio": round(ctx.bar.volume / (vol_sma or 1.0), 2),
                },
            )

        # Fresh Short Breakdown: Close crosses below 24h low with confirmed sell flow
        if (
            current_close < channel_low
            and prev_close >= prev_channel_low
            and book_imb <= -self.imbalance_thresh
            and vol_active
        ):
            confidence = min(0.9, 0.6 + max(0.0, -book_imb) * 0.5)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fresh_channel_breakout_low_flow_confirmed",
                    "channel_low": channel_low,
                    "close": current_close,
                    "book_imbalance": book_imb,
                    "volume_ratio": round(ctx.bar.volume / (vol_sma or 1.0), 2),
                },
            )

        return None