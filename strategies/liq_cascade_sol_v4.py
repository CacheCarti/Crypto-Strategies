from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolCascadeExhaustion(Strategy):
    METADATA = {
        "name": "SOL Cascade Exhaustion",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 540.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 30,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 16
        self.vol_period = 16
        self.range_mult = 2.2
        self.vol_mult = 2.0
        self.cooldown_bars = 6

        self.pending_cascade = False
        self.cascade_bar = -1
        self.cascade_low = 0.0
        self.cascade_high = 0.0
        self.cascade_vol_ratio = 0.0
        self.cascade_range_ratio = 0.0
        self.last_trade_bar = -999

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.pending_cascade = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n = 35
        closes = ctx.closes(n)
        highs = ctx.highs(n)
        lows = ctx.lows(n)
        volumes = ctx.volumes(n)

        if len(closes) < n:
            return None

        bar = ctx.bar
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], self.atr_period)
        avg_vol = self._sma(volumes[:-1], self.vol_period)
        rsi = self._rsi(closes, 14) or 50.0

        if atr is None or avg_vol is None or avg_vol <= 0:
            return None

        # Position management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Take profit early if RSI is heavily overbought
                if rsi > 76.0:
                    return ctx.signal("flat", confidence=0.7, metadata={
                        "reason": "rsi_overbought_exit",
                        "rsi": round(rsi, 2),
                        "price": bar.close
                    })
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Check for confirmed stabilization after a cascade
        if self.pending_cascade and ctx.bar_index == self.cascade_bar + 1:
            self.pending_cascade = False
            held_low = bar.low >= self.cascade_low * 0.9985
            rebounded = bar.close > bar.open or bar.close > (self.cascade_low + (self.cascade_high - self.cascade_low) * 0.35)

            if held_low and rebounded:
                self.last_trade_bar = ctx.bar_index
                
                # Context modifiers
                funding_sol = ctx.features.get("funding_rate_solusdt", 0.0)
                fg_index = ctx.features.get("fear_greed_index", 50.0)
                
                # Base confidence adjusted by exhaustion signs
                conf = 0.65
                if funding_sol < 0.0:  # Short crowding during dump
                    conf += 0.10
                if rsi < 35.0:
                    conf += 0.10
                conf = min(0.95, max(0.50, conf))

                # Tight stop loss under cascade low (in bps), clamped within safe ranges
                calc_sl_bps = max(180.0, min(360.0, ((bar.close - self.cascade_low) / bar.close) * 10000.0 + 35.0))

                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=calc_sl_bps,
                    take_profit_bps=540.0,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "liquidation_cascade_stabilization_long",
                        "cascade_low": round(self.cascade_low, 3),
                        "cascade_high": round(self.cascade_high, 3),
                        "range_ratio": round(self.cascade_range_ratio, 2),
                        "vol_ratio": round(self.cascade_vol_ratio, 2),
                        "rsi": round(rsi, 2),
                        "funding_rate": round(funding_sol, 6),
                        "fear_greed": round(fg_index, 1)
                    }
                )

        # Detect high-volume panic flush (Cascade bar)
        bar_range = bar.high - bar.low
        is_bearish = bar.close < bar.open
        drop_pct = (bar.open - bar.close) / bar.open if bar.open > 0 else 0.0
        close_in_lower_quarter = (bar.close - bar.low) <= (bar_range * 0.35) if bar_range > 0 else False

        range_ratio = bar_range / atr
        vol_ratio = bar.volume / avg_vol

        if (is_bearish and 
            drop_pct >= 0.012 and 
            range_ratio >= self.range_mult and 
            vol_ratio >= self.vol_mult and 
            close_in_lower_quarter):
            
            self.pending_cascade = True
            self.cascade_bar = ctx.bar_index
            self.cascade_low = bar.low
            self.cascade_high = bar.high
            self.cascade_range_ratio = range_ratio
            self.cascade_vol_ratio = vol_ratio

        return None