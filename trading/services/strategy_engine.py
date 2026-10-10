from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import logging
from decimal import Decimal

from trading.models import MomentumScore, RebalanceEvent, Stock, TradingSignal
from trading.services.momentum_calculator import get_momentum_calculator
from trading.services.snaptrade_client import get_trading_executor
from portfolio.models import Portfolio

logger = logging.getLogger(__name__)


class MomentumTradingStrategy:
    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio
        self.momentum_calculator = get_momentum_calculator()
        self.trading_executor = get_trading_executor()
        self.rebalance_frequency = getattr(settings, "REBALANCE_FREQUENCY", "weekly")

    def should_rebalance(self) -> bool:
        last_rebalance = (
            RebalanceEvent.objects.filter(execution_status="COMPLETED")
            .order_by("-date")
            .first()
        )

        if not last_rebalance:
            return True

        days_since_rebalance = (timezone.now().date() - last_rebalance.date).days

        if self.rebalance_frequency == "weekly":
            return days_since_rebalance >= 7
        elif self.rebalance_frequency == "monthly":
            return days_since_rebalance >= 30
        else:
            return False

    def execute_rebalance(self, calculation_date: datetime = None) -> RebalanceEvent:
        if calculation_date is None:
            calculation_date = timezone.now().date()

        logger.info(f"Starting rebalance for {calculation_date}")

        rebalance_event = RebalanceEvent.objects.create(
            date=calculation_date,
            total_stocks_analyzed=0,
            buy_signals_generated=0,
            sell_signals_generated=0,
            execution_status="IN_PROGRESS",
        )

        try:
            # Step 1: Update stock universe and calculate momentum scores
            stocks = self.momentum_calculator.update_stock_universe()
            momentum_scores = self.momentum_calculator.calculate_momentum_scores_bulk(
                stocks, calculation_date
            )
            rebalance_event.total_stocks_analyzed = len(momentum_scores)

            # Step 2: Rank stocks and determine quintiles
            self.momentum_calculator.rank_stocks_by_momentum(calculation_date)

            # Step 3: Generate trading signals
            buy_signals, sell_signals = self.generate_trading_signals(
                calculation_date
            )
            rebalance_event.buy_signals_generated = len(buy_signals)
            rebalance_event.sell_signals_generated = len(sell_signals)
            rebalance_event.save()

            # Step 4: Execute trades
            self.execute_trading_signals(buy_signals, sell_signals, rebalance_event)

            # Step 5: Update portfolio value
            self.portfolio.calculate_total_value()
            self.portfolio.save()

            rebalance_event.total_portfolio_value = self.portfolio.total_value
            rebalance_event.execution_status = "COMPLETED"
            rebalance_event.completed_at = timezone.now()
            rebalance_event.save()
            logger.info(f"Rebalance completed successfully for {calculation_date}")
        except Exception as e:  # noqa: BLE001 - persist failure for visibility
            logger.exception("Rebalance failed")
            rebalance_event.execution_status = "FAILED"
            rebalance_event.error_message = str(e)
            rebalance_event.completed_at = timezone.now()
            rebalance_event.save()

        return rebalance_event

    def generate_trading_signals(
        self, calculation_date: datetime = None
    ) -> Tuple[List[TradingSignal], List[TradingSignal]]:
        if calculation_date is None:
            calculation_date = timezone.now().date()

        # Get current portfolio positions
        current_positions = {
            pos.stock: pos for pos in self.portfolio.get_current_positions()
        }

        # Get top quintile stocks (buy candidates)
        top_quintile_stocks = self.momentum_calculator.get_top_quintile_stocks(
            calculation_date
        )

        # Get bottom quintile stocks (sell candidates)
        bottom_quintile_stocks = self.momentum_calculator.get_bottom_quintile_stocks(
            calculation_date
        )

        buy_signals = []
        sell_signals = []

        # Generate sell signals for positions in bottom quintile
        for stock in bottom_quintile_stocks:
            if stock in current_positions:
                position = current_positions[stock]
                momentum_score = stock.momentum_scores.filter(
                    calculation_date=calculation_date
                ).first()

                sell_signal = TradingSignal.objects.create(
                    stock=stock,
                    signal_date=calculation_date,
                    signal_type="SELL",
                    momentum_score=momentum_score,
                    target_quantity=position.quantity,
                    target_value=position.current_value,
                    reason=f"Stock in bottom quintile (rank {momentum_score.rank if momentum_score else 'N/A'})",
                )
                sell_signals.append(sell_signal)

        # Generate buy signals for top quintile stocks not in portfolio
        available_cash = self.trading_executor.get_available_cash_for_trading(
            self.portfolio
        )

        # Filter out stocks already in portfolio
        buy_candidates = [
            stock for stock in top_quintile_stocks if stock not in current_positions
        ]

        if buy_candidates and available_cash > 0:
            # Calculate equal weight allocation
            allocation_per_stock = available_cash / len(buy_candidates)

            for stock in buy_candidates:
                momentum_score = stock.momentum_scores.filter(
                    calculation_date=calculation_date
                ).first()

                buy_signal = TradingSignal.objects.create(
                    stock=stock,
                    signal_date=calculation_date,
                    signal_type="BUY",
                    momentum_score=momentum_score,
                    target_value=allocation_per_stock,
                    reason=f"Stock in top quintile (rank {momentum_score.rank if momentum_score else 'N/A'})",
                )
                buy_signals.append(buy_signal)

        logger.info(
            f"Generated {len(buy_signals)} buy signals and {len(sell_signals)} sell signals"
        )

        return buy_signals, sell_signals

    def execute_trading_signals(
        self,
        buy_signals: List[TradingSignal],
        sell_signals: List[TradingSignal],
        rebalance_event: RebalanceEvent,
    ):
        user_secret = self.portfolio.snaptrade_user_secret or None

        # Without a connected brokerage we can still compute signals, but we
        # cannot place orders - stop execution here and leave signals pending.
        if self.trading_executor.snaptrade is None or not self.portfolio.is_connected:
            logger.warning(
                "Portfolio is not connected to SnapTrade; signals were generated "
                "but no orders were placed."
            )
            return

        # Execute sell orders first to free up cash.
        sell_stocks = [signal.stock for signal in sell_signals]
        sell_trades = self.trading_executor.execute_sell_orders(
            self.portfolio, sell_stocks, user_secret=user_secret
        )

        for signal in sell_signals:
            signal.is_executed = True
            signal.executed_at = timezone.now()
            signal.save()

        # Calculate total value available for buying (cash + sale proceeds).
        total_sell_value = sum(
            signal.target_value for signal in sell_signals if signal.target_value
        )
        available_for_buying = self.portfolio.current_cash + total_sell_value

        buy_stocks = [signal.stock for signal in buy_signals]
        buy_trades = []
        if buy_stocks and available_for_buying > 0:
            buy_trades = self.trading_executor.execute_buy_orders(
                self.portfolio,
                buy_stocks,
                available_for_buying,
                user_secret=user_secret,
            )

            for signal in buy_signals:
                signal.is_executed = True
                signal.executed_at = timezone.now()
                signal.save()

        logger.info(
            f"Executed {len(sell_trades)} sell orders and {len(buy_trades)} buy orders"
        )

    def get_strategy_performance(self, days_back: int = 30) -> Dict:
        start_date = timezone.now().date() - timedelta(days=days_back)

        # Get recent rebalance events
        recent_rebalances = RebalanceEvent.objects.filter(
            date__gte=start_date, execution_status="COMPLETED"
        ).order_by("-date")

        # Get recent trading signals
        recent_signals = TradingSignal.objects.filter(
            signal_date__gte=start_date, is_executed=True
        )

        # Get portfolio performance
        performance_metrics = self.portfolio.performance_metrics.filter(
            date__gte=start_date
        ).order_by("-date")

        buy_signals_count = recent_signals.filter(signal_type="BUY").count()
        sell_signals_count = recent_signals.filter(signal_type="SELL").count()

        # Calculate returns if we have performance data
        returns_data = None
        if performance_metrics.count() >= 2:
            latest = performance_metrics.first()
            earliest = performance_metrics.last()

            if earliest.total_value > 0:
                period_return = (
                    (latest.total_value - earliest.total_value) / earliest.total_value
                ) * 100

                returns_data = {
                    "period_return_percent": float(period_return),
                    "start_value": float(earliest.total_value),
                    "end_value": float(latest.total_value),
                    "start_date": earliest.date,
                    "end_date": latest.date,
                }

        return {
            "rebalance_events": recent_rebalances.count(),
            "buy_signals": buy_signals_count,
            "sell_signals": sell_signals_count,
            "current_portfolio_value": float(self.portfolio.total_value),
            "current_cash": float(self.portfolio.current_cash),
            "active_positions": self.portfolio.positions.filter(quantity__gt=0).count(),
            "returns": returns_data,
        }

    def validate_strategy_setup(self) -> Dict:
        validation_result = {"is_valid": True, "issues": [], "warnings": []}

        # Check portfolio setup
        if not self.portfolio.snaptrade_user_id:
            validation_result["issues"].append("Portfolio missing SnapTrade user ID")
            validation_result["is_valid"] = False

        if not self.portfolio.snaptrade_account_id:
            validation_result["issues"].append("Portfolio missing SnapTrade account ID")
            validation_result["is_valid"] = False

        # Check API keys
        if not settings.MASSIVE_API_KEY:
            validation_result["issues"].append("Massive API key not configured")
            validation_result["is_valid"] = False

        if not settings.SNAPTRADE_CLIENT_ID or not settings.SNAPTRADE_CLIENT_SECRET:
            validation_result["issues"].append(
                "SnapTrade API credentials not configured"
            )
            validation_result["is_valid"] = False

        # Check data availability
        active_stocks = Stock.objects.filter(is_active=True).count()
        if active_stocks == 0:
            validation_result["warnings"].append("No active stocks in database")

        # Check recent momentum calculations
        recent_scores = self.momentum_calculator.get_momentum_statistics()
        if not recent_scores:
            validation_result["warnings"].append("No recent momentum scores calculated")

        return validation_result

    def run_backtest(
        self,
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal = Decimal("100000"),
    ) -> Dict:
        """A simple long-only, equal-weight, monthly-rebalanced backtest.

        Momentum is read from stored :class:`MomentumScore` history and returns
        are computed from stored :class:`PriceData`. It is intentionally
        lightweight, but it produces real numbers when the database has been
        populated (run a rebalance first).
        """
        logger.info(f"Running backtest from {start_date} to {end_date}")

        results = {
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": float(initial_capital),
            "final_value": float(initial_capital),
            "total_return": 0.0,
            "periods": 0,
            "error": None,
        }

        rebalance_dates = list(
            MomentumScore.objects.filter(
                calculation_date__gte=start_date, calculation_date__lte=end_date
            )
            .values_list("calculation_date", flat=True)
            .distinct()
            .order_by("calculation_date")
        )

        if len(rebalance_dates) < 2:
            results["error"] = (
                "Insufficient momentum history for a backtest. Run rebalances "
                "to populate MomentumScore data first."
            )
            return results

        capital = initial_capital
        for index in range(len(rebalance_dates) - 1):
            current_date = rebalance_dates[index]
            next_date = rebalance_dates[index + 1]

            top_scores = MomentumScore.objects.filter(
                calculation_date=current_date, is_top_quintile=True
            ).select_related("stock")
            if not top_scores:
                continue

            allocations = capital / len(top_scores)
            period_value = Decimal("0")

            for score in top_scores:
                start_price = (
                    score.stock.price_data.filter(date__gte=current_date)
                    .order_by("date")
                    .values_list("close", flat=True)
                    .first()
                )
                end_price = (
                    score.stock.price_data.filter(date__lte=next_date)
                    .order_by("-date")
                    .values_list("close", flat=True)
                    .first()
                )
                if start_price and end_price and start_price > 0:
                    period_value += allocations * (end_price / start_price)
                else:
                    period_value += allocations

            capital = period_value
            results["periods"] += 1

        results["final_value"] = float(capital)
        if initial_capital > 0:
            results["total_return"] = float(
                (capital - initial_capital) / initial_capital * 100
            )
        return results


def get_strategy_engine(portfolio: Portfolio) -> MomentumTradingStrategy:
    return MomentumTradingStrategy(portfolio)