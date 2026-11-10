from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.trend_ema_period = 50
        self.vol_mult = 2.2
        self.range_mult = 1.7
        self.cooldown_bars = 10
        self.max_hold_bars = 5
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.trend_ema_period + 5)
        highs = ctx.highs(self.trend_ema_period + 5)
        lows = ctx.lows(self.trend_ema_period + 5)
        volumes = ctx.volumes(self.vol_period + 5)

        if len(closes) < self.trend_ema_period or len(volumes) < self.vol_period + 1:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based max hold exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_bars_held_reached", "bars_held": bars_held}
                )

            # Momentum stall: reversal candle closing against the trade
            curr_bar = ctx.bar
            if pos_dir == "long" and curr_bar.close < curr_bar.open:
                # If momentum reverses strongly
                body = curr_bar.open - curr_bar.close
                candle_range = curr_bar.high - curr_bar.low
                if candle_range > 0 and (body / candle_range) > 0.6:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "bearish_stall_reversal", "bars_held": bars_held}
                    )
            elif pos_dir == "short" and curr_bar.close > curr_bar.open:
                body = curr_bar.close - curr_bar.open
                candle_range = curr_bar.high - curr_bar.low
                if candle_range > 0 and (body / candle_range) > 0.6:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "bullish_stall_reversal", "bars_held": bars_held}
                    )
            return None

        # Cooldown guard after last exit to prevent overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: prioritize strong trend alignment and non-crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.4 or market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Technical Indicators
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        current_vol = volumes[-1]
        atr = self._atr(highs, lows, closes, self.atr_period)
        ema_50 = self._ema(closes, self.trend_ema_period)

        if avg_vol <= 0 or atr is None or ema_50 is None:
            return None

        curr_bar = ctx.bar
        bar_range = curr_bar.high - curr_bar.low
        body = abs(curr_bar.close - curr_bar.open)

        # Volume burst: current volume > 2.2x average 20-bar volume
        vol_surge = (current_vol / avg_vol) >= self.vol_mult
        # Range burst: candle range > 1.7x ATR and solid body (> 55% of candle range)
        range_surge = (bar_range >= self.range_mult * atr) and (bar_range > 0 and (body / bar_range) >= 0.55)

        if not (vol_surge and range_surge):
            return None

        # Macro trend alignment & cross-asset filter (BTC 24h return)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Long burst: strong bullish candle closing well above EMA 50, BTC not heavily dumping
        if curr_bar.close > curr_bar.open and curr_bar.close > ema_50 and btc_ret > -2.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + (current_vol / (avg_vol * 3.0)) * 0.2)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_burst_bullish_expansion",
                    "vol_ratio": round(current_vol / avg_vol, 2),
                    "range_atr_ratio": round(bar_range / atr, 2),
                    "ema50": round(ema_50, 2),
                    "close": curr_bar.close,
                    "btc_ret": btc_ret
                }
            )

        # Short burst: strong bearish candle closing well below EMA 50, BTC not ripping upwards
        if curr_bar.close < curr_bar.open and curr_bar.close < ema_50 and btc_ret < 2.0:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + (current_vol / (avg_vol * 3.0)) * 0.2)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vol_burst_bearish_expansion",
                    "vol_ratio": round(current_vol / avg_vol, 2),
                    "range_atr_ratio": round(bar_range / atr, 2),
                    "ema50": round(ema_50, 2),
                    "close": curr_bar.close,
                    "btc_ret": btc_ret
                }
            )

        return None