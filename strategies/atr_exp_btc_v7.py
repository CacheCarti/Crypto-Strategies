from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityExpansionMomentum(Strategy):
    METADATA = {
        "name": "Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 640.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 70,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.atr_baseline_period = 50
        self.expansion_mult = 1.52
        self.mom_lookback = 12
        self.mom_atr_mult = 1.80
        self.cooldown_bars = 12
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _atr_series(self, highs, lows, closes, period, count):
        if len(closes) < period + count:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atrs = []
        for j in range(count):
            end_idx = len(trs) - count + 1 + j
            start_idx = end_idx - period
            if start_idx < 0:
                continue
            atrs.append(sum(trs[start_idx:end_idx]) / period)
        return atrs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.atr_baseline_period + self.atr_period + self.mom_lookback + 5
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if len(closes) < req_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None or current_atr <= 0:
            return None

        atr_hist = self._atr_series(highs, lows, closes, self.atr_period, self.atr_baseline_period)
        if not atr_hist or len(atr_hist) < self.atr_baseline_period:
            return None

        atr_baseline = sum(atr_hist) / len(atr_hist)
        if atr_baseline <= 0:
            return None

        vol_ratio = current_atr / atr_baseline
        price_now = closes[-1]
        price_past = closes[-1 - self.mom_lookback]
        net_move = price_now - price_past
        mom_ratio = abs(net_move) / current_atr

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Exit when volatility fully contracts back below baseline
            if vol_ratio < 0.95:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "volatility_contraction_exit",
                        "vol_ratio": round(vol_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "atr_baseline": round(atr_baseline, 2),
                    }
                )

            # Reversal exit if directional momentum sharply inverts against position
            if pos_dir == "long" and net_move < -1.4 * current_atr:
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "momentum_inversion_exit",
                        "net_move": round(net_move, 2),
                        "pos_dir": pos_dir,
                    }
                )
            if pos_dir == "short" and net_move > 1.4 * current_atr:
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "momentum_inversion_exit",
                        "net_move": round(net_move, 2),
                        "pos_dir": pos_dir,
                    }
                )
            return None

        # Hard cooldown check after closing a position
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: High-conviction volatility expansion with strong price displacement
        is_vol_expanding = vol_ratio >= self.expansion_mult
        is_strong_move = mom_ratio >= self.mom_atr_mult

        if is_vol_expanding and is_strong_move:
            base_conf = min(0.92, 0.60 + 0.15 * (vol_ratio - self.expansion_mult) + 0.10 * (mom_ratio - self.mom_atr_mult))

            # Long entry: directional surge upward without excessive long funding crowding
            if net_move > 0 and funding < 0.0004:
                return ctx.signal(
                    "long",
                    confidence=round(base_conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bull_momentum",
                        "vol_ratio": round(vol_ratio, 3),
                        "mom_ratio": round(mom_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "atr_baseline": round(atr_baseline, 2),
                        "funding": round(funding, 6),
                    }
                )

            # Short entry: directional breakdown downward without excessive short funding crowding
            elif net_move < 0 and funding > -0.0004:
                return ctx.signal(
                    "short",
                    confidence=round(base_conf, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bear_momentum",
                        "vol_ratio": round(vol_ratio, 3),
                        "mom_ratio": round(mom_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "atr_baseline": round(atr_baseline, 2),
                        "funding": round(funding, 6),
                    }
                )

        return None