from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class EthBtcRelativeMomentum(Strategy):
    METADATA = {
        "name": "ETH-BTC Relative Momentum",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.spread_threshold = 1.0
        self.spread_exit_threshold = 0.1
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.ema_period = 24
        self.rsi_period = 14

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Fetch returns and calculate relative strength spread
        eth_ret = ctx.features.get("eth_return_pct", 0.0)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Standardize return scale (handles decimal format e.g. 0.02 vs percentage format 2.0)
        raw_spread = eth_ret - btc_ret
        spread = raw_spread * 100.0 if -0.5 < raw_spread < 0.5 and (abs(eth_ret) < 0.5 and abs(btc_ret) < 0.5) else raw_spread

        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        current_close = ctx.bar.close
        pos_dir = ctx.position_direction()

        # Manage existing open positions
        if pos_dir == "long":
            if spread < self.spread_exit_threshold or (rsi is not None and rsi > 80.0):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_relative_momentum_faded",
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "rsi": round(rsi, 2) if rsi else 50.0,
                        "close": round(current_close, 2),
                    },
                )
            return None

        if pos_dir == "short":
            if spread > -self.spread_exit_threshold or (rsi is not None and rsi < 20.0):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_relative_lag_faded",
                        "spread": round(spread, 3),
                        "eth_ret": round(eth_ret, 3),
                        "btc_ret": round(btc_ret, 3),
                        "rsi": round(rsi, 2) if rsi else 50.0,
                        "close": round(current_close, 2),
                    },
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: skip entering during severe crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: ETH leading BTC by > 1.0%
        if spread >= self.spread_threshold:
            confidence = min(0.65 + min(spread - self.spread_threshold, 2.0) * 0.1, 0.85)
            return ctx.signal(
                "long",
                confidence=confidence,
                metadata={
                    "reason": "eth_relative_strength_outperformance",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2) if rsi else 50.0,
                    "ema": round(ema, 2) if ema else current_close,
                    "close": round(current_close, 2),
                },
            )

        # Entry logic: ETH lagging BTC by < -1.0%
        if spread <= -self.spread_threshold:
            confidence = min(0.65 + min(-spread - self.spread_threshold, 2.0) * 0.1, 0.85)
            return ctx.signal(
                "short",
                confidence=confidence,
                metadata={
                    "reason": "eth_relative_weakness_underperformance",
                    "spread": round(spread, 3),
                    "eth_ret": round(eth_ret, 3),
                    "btc_ret": round(btc_ret, 3),
                    "rsi": round(rsi, 2) if rsi else 50.0,
                    "ema": round(ema, 2) if ema else current_close,
                    "close": round(current_close, 2),
                },
            )

        return None