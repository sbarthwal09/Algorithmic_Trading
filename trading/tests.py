from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from portfolio.models import Portfolio
from trading.models import MomentumScore, RebalanceEvent, Stock, TradingSignal
from trading.services.strategy_engine import get_strategy_engine

CALC_DATE = date(2026, 1, 2)


def _make_stock_with_score(index: int, score: Decimal) -> Stock:
    stock = Stock.objects.create(ticker=f"T{index:03d}", name=f"Stock {index}")
    MomentumScore.objects.create(
        stock=stock,
        calculation_date=CALC_DATE,
        momentum_score=score,
        period_start=date(2025, 1, 2),
        period_end=date(2025, 12, 2),
    )
    return stock


class MomentumQuintileTests(TestCase):
    def test_calculate_quintiles_for_date(self):
        for index in range(25):
            _make_stock_with_score(index, Decimal(index) / Decimal("100"))

        MomentumScore.calculate_quintiles_for_date(CALC_DATE)

        self.assertEqual(MomentumScore.objects.filter(quintile=1).count(), 5)
        self.assertEqual(MomentumScore.objects.filter(quintile=5).count(), 5)

        top = MomentumScore.objects.get(rank=1)
        self.assertEqual(top.momentum_score, Decimal("0.240000"))
        self.assertTrue(top.is_top_quintile)

    def test_quintiles_empty_date_is_noop(self):
        MomentumScore.calculate_quintiles_for_date(date(2000, 1, 1))


class ViewSmokeTests(TestCase):
    def setUp(self):
        self.portfolio = Portfolio.objects.create(
            name="View Portfolio",
            initial_cash=Decimal("5000"),
            current_cash=Decimal("5000"),
        )

    def test_pages_render(self):
        urls = [
            reverse("trading:dashboard"),
            reverse("trading:portfolio_list"),
            reverse("trading:portfolio_create"),
            reverse("trading:portfolio_detail", args=[self.portfolio.pk]),
            reverse("trading:portfolio_delete", args=[self.portfolio.pk]),
            reverse("trading:momentum_scores"),
            reverse("trading:trading_signals"),
            reverse("trading:snaptrade_success"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_create_portfolio_via_post(self):
        response = self.client.post(
            reverse("trading:portfolio_create"),
            {
                "name": "New Portfolio",
                "description": "created in test",
                "initial_cash": "1000",
                "current_cash": "1000",
                "snaptrade_user_id": "",
                "snaptrade_account_id": "",
                "snaptrade_user_secret": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Portfolio.objects.filter(name="New Portfolio").exists())


class StrategyEngineTests(TestCase):
    def setUp(self):
        self.portfolio = Portfolio.objects.create(
            name="Strategy Portfolio",
            initial_cash=Decimal("10000"),
            current_cash=Decimal("10000"),
        )
        for index in range(10):
            _make_stock_with_score(index, Decimal(index) / Decimal("100"))
        MomentumScore.calculate_quintiles_for_date(CALC_DATE)

    def test_generate_trading_signals(self):
        strategy = get_strategy_engine(self.portfolio)
        buys, sells = strategy.generate_trading_signals(CALC_DATE)

        self.assertEqual(len(buys), 2)
        self.assertEqual(len(sells), 0)
        self.assertEqual(TradingSignal.objects.filter(signal_type="BUY").count(), 2)

    def test_execute_signals_skipped_when_not_connected(self):
        strategy = get_strategy_engine(self.portfolio)
        buys, sells = strategy.generate_trading_signals(CALC_DATE)
        event = RebalanceEvent.objects.create(
            date=CALC_DATE,
            total_stocks_analyzed=10,
            buy_signals_generated=len(buys),
            sell_signals_generated=len(sells),
            execution_status="IN_PROGRESS",
        )

        strategy.execute_trading_signals(buys, sells, event)

        self.assertFalse(
            TradingSignal.objects.filter(is_executed=True).exists()
        )
        self.assertEqual(self.portfolio.trades.count(), 0)
