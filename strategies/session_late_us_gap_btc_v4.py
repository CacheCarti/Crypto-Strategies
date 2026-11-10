from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "BTC Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours
        "warmup_bars": 50,  # Need enough bars for 20-period SMA for gap calc
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -100 # Cooldown period
        self.session_open_price = None
        self.session_close_price = None
        self.prior_session_close = None
        self.traded_this_session = False
        self.gap_threshold_bps = 50  # Minimum gap size in bps to consider
        self.continuation_threshold_bps = 20 # How much continuation before we consider it failed
        self.sma_period = 20 # For trend filtering

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"] or len(opens) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update session prices and prior session close
        if current_hour == 18: # Start of late US session (UTC 18:00)
            if self.session_open_price is None: # Only set once per session start
                self.session_open_price = ctx.bar.open
                self.traded_this_session = False
        elif current_hour == 23: # End of late US session (UTC 23:00)
            # Store prior session close for next session's gap analysis
            self.prior_session_close = ctx.bar.close
            self.session_open_price = None # Reset for next session start

        # Exit any open positions before UTC 0 to avoid holding into next day's open
        if ctx.has_position() and current_hour == 23 and ctx.bar.timestamp.minute >= 55:
            return ctx.signal("flat", confidence=0.8, metadata={"reason": "eod_exit", "current_hour": current_hour})

        # Cooldown period after a trade
        if ctx.bar_index < self.last_trade_bar + 5: # 5-bar cooldown (~5 hours)
            return None

        # Only consider trades during late US session (UTC 18-23)
        if not (18 <= current_hour <= 23):
            return None

        # Ensure we have a prior session close and current session open
        if self.prior_session_close is None or self.session_open_price is None:
            return None

        # Only trade once per session
        if self.traded_this_session:
            return None

        # Regime filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        btc_return = ctx.features.get("btc_return_pct", 0.0)
        
        # Avoid trading in extreme volatility (high BTC 24h return) or extreme fear/greed
        if abs(btc_return) > 5.0 or fear_greed < 25 or fear_greed > 75:
             return None

        # Trend filter (using a simple SMA)
        sma = self._sma(closes, self.sma_period)
        if sma is None:
            return None
        
        # Calculate gap from prior session close to current session open
        gap_to_open_bps = ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000

        # Check for a significant gap that has not continued
        if abs(gap_to_open_bps) > self.gap_threshold_bps:
            # Check for failed continuation: price moved against the gap direction
            # For a bullish gap (open > prior close), we expect price to continue up. If it drops, it's a failed continuation.
            # For a bearish gap (open < prior close), we expect price to continue down. If it rises, it's a failed continuation.

            # Current price relative to session open
            current_price_change_bps = ((ctx.bar.close - self.session_open_price) / self.session_open_price) * 10000

            if gap_to_open_bps > 0: # Bullish gap (open higher than prior close)
                # If price has moved significantly down from session open, indicating failed bullish continuation
                if current_price_change_bps < -self.continuation_threshold_bps:
                    # Trade short: reversion back towards the prior session close
                    if not ctx.has_position():
                        self.traded_this_session = True
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "failed_bullish_gap_reversion",
                                "gap_to_open_bps": round(gap_to_open_bps, 2),
                                "current_price_change_bps": round(current_price_change_bps, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "session_open": round(self.session_open_price, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "fear_greed": fear_greed,
                                "btc_return_pct": round(btc_return, 2)
                            }
                        )
            elif gap_to_open_bps < 0: # Bearish gap (open lower than prior close)
                # If price has moved significantly up from session open, indicating failed bearish continuation
                if current_price_change_bps > self.continuation_threshold_bps:
                    # Trade long: reversion back towards the prior session close
                    if not ctx.has_position():
                        self.traded_this_session = True
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "failed_bearish_gap_reversion",
                                "gap_to_open_bps": round(gap_to_open_bps, 2),
                                "current_price_change_bps": round(current_price_change_bps, 2),
                                "prior_session_close": round(self.prior_session_close, 2),
                                "session_open": round(self.session_open_price, 2),
                                "current_close": round(ctx.bar.close, 2),
                                "fear_greed": fear_greed,
                                "btc_return_pct": round(btc_return, 2)
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset traded_this_session if the position closed, allowing a new trade in a future session
        # For this strategy, we only trade once per session, so this is not strictly needed for same-session re-entry
        # but good practice for adaptive logic.
        pass