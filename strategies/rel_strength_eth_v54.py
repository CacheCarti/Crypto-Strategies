from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcRelativeStrengthMomentum(Strategy):
    METADATA = {
        "name": "EthBtcRelativeStrengthMomentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["eth_return_pct", "btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.spread_threshold = 2.0
        self.rsi_period = 14
        self.atr_period = 14
        self.ema_period = 20

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

    def _ema(self, values, period):
        if len(values) < period:
            return values[-1]
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr_pct(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.015
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr_val = sum(trs[-period:]) / period
        return atr_val / closes[-1] if closes[-1] > 0 else 0.015

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Market regime gating: avoid extreme crisis environments
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        spread = eth_ret - btc_ret

        highs = ctx.highs(25)
        lows = ctx.lows(25)
        current_close = ctx.bar.close

        rsi = self._rsi(closes, self.rsi_period)
        ema20 = self._ema(closes, self.ema_period)
        atr_pct = self._atr_pct(highs, lows, closes, self.atr_period)

        # Volatility state confirmation: avoid trades during dead markets or parabolic blowoffs
        vol_valid = 0.005 <= atr_pct <= 0.045

        # Check exits for open positions (mean-reversion of relative spread or momentum exhaustion)
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                if spread < 0.2 or rsi > 72.0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_spread_converged", "spread": spread, "rsi": rsi})
            elif direction == "short":
                if spread > -0.2 or rsi < 28.0:
                    return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_spread_converged", "spread": spread, "rsi": rsi})
            return None

        # Cooldown guard after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry logic: ETH strength supported by price trend and healthy RSI momentum
        if vol_valid:
            if spread > self.spread_threshold and current_close > ema20 and 48.0 <= rsi <= 68.0:
                conf = min(0.85, 0.55 + (spread - self.spread_threshold) * 0.05)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={"reason": "eth_outperformance_trend", "spread": spread, "rsi": rsi, "atr_pct": atr_pct, "close": current_close, "ema20": ema20}
                )

            elif spread < -self.spread_threshold and current_close < ema20 and 32.0 <= rsi <= 52.0:
                conf = min(0.85, 0.55 + (-spread - self.spread_threshold) * 0.05)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={"reason": "eth_underperformance_trend", "spread": spread, "rsi": rsi, "atr_pct": atr_pct, "close": current_close, "ema20": ema20}
                )

        return None