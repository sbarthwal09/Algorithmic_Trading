from django.contrib import admin
from .models import Stock, PriceData, MomentumScore, TradingSignal, RebalanceEvent

@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("ticker", "name", "sector", "is_active", "created_at")
    list_filter = ("is_active", "sector")
    search_fields = ("ticker", "name")
    ordering = ("created_at", "updated_at")

@admin.register(PriceData)
class PriceDataAdmin(admin.ModelAdmin):
    list_display = ("Stock", "date", "close", "volume")
    list_filter = ("date", "Stock")
    search_fields = ("Stock__ticker",)
    ordering = ("created_at",)
    date_hierarchy = "date"

@admin.register(MomentumScore)
class MomentumScoreAdmin(admin.ModelAdmin):
    list_display = ("Stock", "calculation_data", "momentum_score", "rank", "quintile", "is_top_quintile")
    list_filter = ("calculation_data", "quintile", "is_top_quintile")
    search_fields = ("Stock__ticker",)
    ordering = ("created_at",)
    date_hierarchy = "calculation_data"

@admin.register(TradingSignal)
class TradingSignalMetricAdmin(admin.ModelAdmin):
    list_display = ("Stock", "signal_date", "signal_type", "is_executed", "created_at")
    list_filter = ("signal_type", "signal_date", "is_executed")
    search_fields = ("Stock__ticker",)
    ordering = ("created_at", "executed_at")
    date_hierarchy = "signal_date"

@admin.register(RebalanceEvent)
class RebalanceEventAdmin(admin.ModelAdmin):
    list_display = ("date", "execution_status", "total_stock_analyzed", "but_signal_generated", "sell_signal_generated")
    list_filter = ("execution_status", "date")
    ordering = ("created_at", "completed_at")
    date_hierarchy = "date"
