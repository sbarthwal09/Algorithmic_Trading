from decimal import Decimal

from django.test import TestCase

from portfolio.models import PerformanceMetric, Portfolio, Position, Trade
from trading.models import Stock


class PortfolioModelTests(TestCase):
    def setUp(self):
        self.portfolio = Portfolio.objects.create(
            name="Test Portfolio",
            initial_cash=Decimal("10000.00"),
            current_cash=Decimal("10000.00"),
        )
        self.stock = Stock.objects.create(ticker="AAPL", name="Apple Inc.")

    def test_position_update_current_value(self):
        position = Position.objects.create(
            portfolio=self.portfolio,
            stock=self.stock,
            quantity=10,
            average_cost=Decimal("100.0000"),
            current_price=Decimal("120.0000"),
        )
        position.update_current_value()

        self.assertEqual(position.current_value, Decimal("1200.00"))
        self.assertEqual(position.unrealized_pnl, Decimal("200.00"))
        self.assertEqual(position.unrealized_pnl_percent, Decimal("20"))

    def test_calculate_total_value(self):
        Position.objects.create(
            portfolio=self.portfolio,
            stock=self.stock,
            quantity=10,
            average_cost=Decimal("100.0000"),
            current_price=Decimal("120.0000"),
            current_value=Decimal("1200.00"),
        )
        total = self.portfolio.calculate_total_value()
        self.assertEqual(total, Decimal("11200.00"))

    def test_buy_trade_updates_position_and_cash(self):
        trade = Trade.objects.create(
            portfolio=self.portfolio,
            stock=self.stock,
            trade_type="BUY",
            quantity=10,
            filled_quantity=10,
            filled_price=Decimal("100.0000"),
            status="FILLED",
        )
        trade.update_position()

        position = Position.objects.get(portfolio=self.portfolio, stock=self.stock)
        self.assertEqual(position.quantity, 10)
        self.assertEqual(position.average_cost, Decimal("100.0000"))
        self.assertEqual(self.portfolio.current_cash, Decimal("9000.00"))

    def test_sell_trade_reduces_position(self):
        buy = Trade.objects.create(
            portfolio=self.portfolio,
            stock=self.stock,
            trade_type="BUY",
            filled_quantity=10,
            filled_price=Decimal("100.0000"),
            quantity=10,
            status="FILLED",
        )
        buy.update_position()

        sell = Trade.objects.create(
            portfolio=self.portfolio,
            stock=self.stock,
            trade_type="SELL",
            filled_quantity=4,
            filled_price=Decimal("150.0000"),
            quantity=4,
            status="FILLED",
        )
        sell.update_position()

        position = Position.objects.get(portfolio=self.portfolio, stock=self.stock)
        self.assertEqual(position.quantity, 6)
        # 10000 - 1000 + 600 = 9600
        self.assertEqual(self.portfolio.current_cash, Decimal("9600.00"))

    def test_record_performance_metric(self):
        metric = self.portfolio.record_performance_metric()
        self.assertIsInstance(metric, PerformanceMetric)
        self.assertEqual(metric.total_value, Decimal("10000.00"))
