import logging

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from portfolio.models import Portfolio
from trading.forms import PortfolioForm
from trading.models import MomentumScore, RebalanceEvent, Stock, TradingSignal
from trading.services.momentum_calculator import get_momentum_calculator
from trading.services.snaptrade_client import get_trading_executor
from trading.services.strategy_engine import get_strategy_engine

logger = logging.getLogger(__name__)


def dashboard(request):
    portfolios = Portfolio.objects.all()
    total_value = portfolios.aggregate(total=Sum("total_value"))["total"] or 0

    latest_date = (
        MomentumScore.objects.order_by("-calculation_date")
        .values_list("calculation_date", flat=True)
        .first()
    )
    top_scores = []
    if latest_date:
        top_scores = (
            MomentumScore.objects.filter(calculation_date=latest_date)
            .select_related("stock")
            .order_by("rank")[:10]
        )

    context = {
        "portfolio_count": portfolios.count(),
        "stock_count": Stock.objects.filter(is_active=True).count(),
        "total_portfolio_value": total_value,
        "latest_momentum_date": latest_date,
        "top_scores": top_scores,
        "recent_signals": TradingSignal.objects.select_related("stock")[:10],
        "recent_rebalances": RebalanceEvent.objects.all()[:5],
    }
    return render(request, "trading/dashboard.html", context)


def portfolio_list(request):
    portfolios = Portfolio.objects.all()
    for portfolio in portfolios:
        portfolio.open_positions = portfolio.get_current_positions().count()
    return render(request, "trading/portfolio_list.html", {"portfolios": portfolios})


def portfolio_detail(request, pk):
    portfolio = get_object_or_404(Portfolio, pk=pk)
    context = {
        "portfolio": portfolio,
        "positions": portfolio.get_current_positions(),
        "trades": portfolio.trades.select_related("stock")[:25],
        "rebalances": RebalanceEvent.objects.all()[:10],
        "performance": portfolio.performance_metrics.all()[:30],
    }
    return render(request, "trading/portfolio_detail.html", context)


def portfolio_create(request):
    if request.method == "POST":
        form = PortfolioForm(request.POST)
        if form.is_valid():
            portfolio = form.save()
            portfolio.calculate_total_value()
            portfolio.save(update_fields=["total_value"])
            messages.success(request, f"Portfolio '{portfolio.name}' created.")
            return redirect("trading:portfolio_detail", pk=portfolio.pk)
    else:
        form = PortfolioForm()
    return render(request, "trading/create_portfolio.html", {"form": form})


def portfolio_delete(request, pk):
    portfolio = get_object_or_404(Portfolio, pk=pk)
    if request.method == "POST":
        name = portfolio.name
        portfolio.delete()
        messages.success(request, f"Portfolio '{name}' deleted.")
        return redirect("trading:portfolio_list")
    return render(request, "trading/delete_portfolio.html", {"portfolio": portfolio})


def momentum_scores(request):
    calculator = get_momentum_calculator()
    calculation_date = parse_date(request.GET.get("date", "") or "")
    if not calculation_date:
        calculation_date = (
            MomentumScore.objects.order_by("-calculation_date")
            .values_list("calculation_date", flat=True)
            .first()
        )

    scores = MomentumScore.objects.none()
    stats = {}
    if calculation_date:
        scores = (
            MomentumScore.objects.filter(calculation_date=calculation_date)
            .select_related("stock")
            .order_by("rank")
        )
        stats = calculator.get_momentum_statistics(calculation_date)

    available_dates = (
        MomentumScore.objects.values_list("calculation_date", flat=True)
        .distinct()
        .order_by("-calculation_date")
    )

    context = {
        "calculation_date": calculation_date,
        "scores": scores,
        "stats": stats,
        "available_dates": available_dates,
    }
    return render(request, "trading/momentum_score.html", context)


def trading_signals(request):
    signal_type = request.GET.get("type")
    signals = TradingSignal.objects.select_related("stock", "momentum_score")
    if signal_type in {"BUY", "SELL", "HOLD"}:
        signals = signals.filter(signal_type=signal_type)
    context = {
        "signals": signals[:200],
        "signal_type": signal_type or "",
    }
    return render(request, "trading/trading_signal.html", context)


@require_POST
def run_rebalance(request, pk):
    portfolio = get_object_or_404(Portfolio, pk=pk)
    strategy = get_strategy_engine(portfolio)
    event = strategy.execute_rebalance()

    if event.execution_status == "COMPLETED":
        messages.success(
            request,
            f"Rebalance completed: {event.total_stocks_analyzed} stocks analysed, "
            f"{event.buy_signals_generated} buys / {event.sell_signals_generated} sells.",
        )
    else:
        messages.error(request, f"Rebalance failed: {event.error_message}")
    return redirect("trading:portfolio_detail", pk=portfolio.pk)


@require_POST
def sync_portfolio(request, pk):
    portfolio = get_object_or_404(Portfolio, pk=pk)
    try:
        executor = get_trading_executor()
        positions = executor.sync_portfolio_positions(portfolio)
        messages.success(
            request, f"Synced {len(positions)} positions from SnapTrade."
        )
    except Exception as e:  # noqa: BLE001 - surface integration errors to the UI
        logger.exception("Portfolio sync failed")
        messages.error(request, f"Sync failed: {e}")
    return redirect("trading:portfolio_detail", pk=portfolio.pk)


def snaptrade_connect(request, pk):
    portfolio = get_object_or_404(Portfolio, pk=pk)
    try:
        executor = get_trading_executor()
        url = executor.get_connection_url(portfolio)
        return redirect(url)
    except Exception as e:  # noqa: BLE001 - surface integration errors to the UI
        logger.exception("SnapTrade connection failed")
        messages.error(request, f"Could not start SnapTrade connection: {e}")
        return redirect("trading:portfolio_detail", pk=portfolio.pk)


def snaptrade_success(request):
    return render(
        request,
        "trading/snaptrade_success.html",
        {"timestamp": timezone.now()},
    )
