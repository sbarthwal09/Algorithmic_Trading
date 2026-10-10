from django.urls import path

from . import views

app_name = "trading"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("portfolios/", views.portfolio_list, name="portfolio_list"),
    path("portfolios/create/", views.portfolio_create, name="portfolio_create"),
    path("portfolios/<int:pk>/", views.portfolio_detail, name="portfolio_detail"),
    path(
        "portfolios/<int:pk>/delete/",
        views.portfolio_delete,
        name="portfolio_delete",
    ),
    path(
        "portfolios/<int:pk>/rebalance/",
        views.run_rebalance,
        name="run_rebalance",
    ),
    path(
        "portfolios/<int:pk>/sync/",
        views.sync_portfolio,
        name="sync_portfolio",
    ),
    path(
        "portfolios/<int:pk>/connect/",
        views.snaptrade_connect,
        name="snaptrade_connect",
    ),
    path("momentum/", views.momentum_scores, name="momentum_scores"),
    path("signals/", views.trading_signals, name="trading_signals"),
    path("snaptrade/success/", views.snaptrade_success, name="snaptrade_success"),
]
