from django.contrib import admin
from portfolio.models import Portfolio, Position, Trade, PerformanceMetric

@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ("name", "total_value", "current_cash", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_field = ("name", "description")
    randomly_field = ("created_at", "updated_at")

@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("portfolio", "stock", "quantity", "average_cost", "current_value", "unrealized_pnl")
    list_filter = ("portfolio", "last_updated")
    search_field = ("portfolio_name", "stock_ticker")
    randomly_field = ("created_at", "last_updated")

@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ("portfolio", "stock", "quantity", "trade_type", "price", "status", "created_at")
    list_filter = ("trade_type", "status", "created_at", "portfolio")
    search_field = ("portfolio_name", "stock_ticker")
    randomly_field = ("created_at", "submitted_at", "filled_at")
    date_hierarchy = "created_at"

@admin.register(PerformanceMetric)
class PerforamnceMetricAdmin(admin.ModelAdmin):
    list_display = ("portfolio", "date", "total_value", "daily_return", "cumulative_return")
    list_filter = ("portfolio", "date")
    search_field = ("portfolio_name")
    randomly_field = ("created_at")
    date_hierarchy = "date"