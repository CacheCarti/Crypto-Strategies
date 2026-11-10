from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Flush Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.045  # Drop >= 4.5% over 24h
        self.cooldown_bars = 14
        self.last_exit_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_midpoint = 0.0
        self.bars_in_trade = 0

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high

        # Position management
        if ctx.has_position():
            self.bars_in_trade += 1
            # Take profit at flush midpoint or time-based cutoff
            if (self.target_midpoint > 0.0 and current_high >= self.target_midpoint) or self.bars_in_trade >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_or_time_exit",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "current_close": current_close,
                        "bars_in_trade": self.bars_in_trade
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Economic flush detection: drop over last 24 bars
        recent_24_closes = closes[-self.lookback:]
        recent_24_highs = highs[-self.lookback:]
        recent_24_lows = lows[-self.lookback:]

        p_start = recent_24_closes[0]
        p_now = recent_24_closes[-1]
        flush_return = (p_now - p_start) / p_start

        if flush_return > self.flush_threshold_pct:
            return None

        # Price stabilization check: current close > previous bar's high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Volatility / cross-asset confirmation
        # 1. RSI oversold condition or recovering from deep stretch
        rsi = self._rsi(closes, period=14)
        if rsi is None or rsi > 42.0:
            return None

        # 2. Avoid entering during outright systemic meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.85 or regime == "MELTDOWN":
            return None

        # 3. Cross-asset relative resilience: BTC 24h return shouldn't be catastrophic collapse (<-10%)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -10.0:
            return None

        # Establish flush boundary & target
        self.flush_high = max(recent_24_highs)
        self.flush_low = min(recent_24_lows)
        self.target_midpoint = (self.flush_high + self.flush_low) / 2.0

        # Ensure reasonable upside exists to target midpoint
        if self.target_midpoint <= current_close * 1.008:
            return None

        self.bars_in_trade = 0

        # Confidence scaled by magnitude of oversold RSI
        conf = min(0.9, max(0.6, (45.0 - rsi) / 25.0))

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "panic_flush_reversal_stabilized",
                "flush_return_pct": round(flush_return * 100, 2),
                "rsi": round(rsi, 2),
                "btc_ret_24h": round(btc_ret, 2),
                "target_midpoint": round(self.target_midpoint, 2),
                "current_close": current_close
            }
        )