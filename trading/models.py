from django.db import models
from django.utils import timezone


class Stock(models.Model):
    ticker = models.CharField(max_length=10, unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    sector = models.CharField(max_length=100, blank=True)
    market_cap = models.BigIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ticker"]

    def __str__(self):
        return self.ticker


class PriceData(models.Model):
    stock = models.ForeignKey(
        Stock, on_delete=models.CASCADE, related_name="price_data"
    )
    date = models.DateField(db_index=True)
    open_price = models.DecimalField(max_digits=12, decimal_places=4)
    high = models.DecimalField(max_digits=12, decimal_places=4)
    low = models.DecimalField(max_digits=12, decimal_places=4)
    close = models.DecimalField(max_digits=12, decimal_places=4)
    volume = models.BigIntegerField()
    adjusted_close = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["stock", "date"], name="unique_stock_price_date"
            )
        ]
        indexes = [
            models.Index(fields=["stock", "date"]),
        ]

    def __str__(self):
        return f"{self.stock.ticker} {self.date}"


class MomentumScore(models.Model):
    stock = models.ForeignKey(
        Stock, on_delete=models.CASCADE, related_name="momentum_scores"
    )
    calculation_date = models.DateField(db_index=True)
    momentum_score = models.DecimalField(max_digits=10, decimal_places=6)
    rank = models.IntegerField(null=True, blank=True)
    quintile = models.IntegerField(null=True, blank=True)
    is_top_quintile = models.BooleanField(default=False)
    period_start = models.DateField()
    period_end = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-calculation_date", "rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["stock", "calculation_date"],
                name="unique_stock_momentum_date",
            )
        ]

    def __str__(self):
        return f"{self.stock.ticker} {self.momentum_score} ({self.calculation_date})"

    @classmethod
    def calculate_quintiles_for_date(cls, calculation_date=None):
        if calculation_date is None:
            calculation_date = timezone.now().date()

        scores = list(
            cls.objects.filter(calculation_date=calculation_date).order_by(
                "-momentum_score"
            )
        )
        if not scores:
            return

        total_stocks = len(scores)
        quintile_size = total_stocks // 5

        updated = []
        for index, score in enumerate(scores):
            score.rank = index + 1
            if quintile_size > 0:
                score.quintile = min(5, (index // quintile_size) + 1)
            else:
                score.quintile = 1
            score.is_top_quintile = score.quintile == 1
            updated.append(score)

        cls.objects.bulk_update(updated, ["rank", "quintile", "is_top_quintile"])


class TradingSignal(models.Model):
    SIGNAL_TYPES = [
        ("BUY", "Buy"),
        ("SELL", "Sell"),
        ("HOLD", "Hold"),
    ]

    stock = models.ForeignKey(
        Stock, on_delete=models.CASCADE, related_name="trading_signals"
    )
    signal_date = models.DateField(db_index=True)
    signal_type = models.CharField(max_length=4, choices=SIGNAL_TYPES)
    momentum_score = models.ForeignKey(
        MomentumScore, on_delete=models.SET_NULL, null=True, blank=True
    )
    target_quantity = models.IntegerField(null=True, blank=True)
    target_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    reason = models.TextField(blank=True)
    is_executed = models.BooleanField(default=False)
    executed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trading_signals"
        ordering = ["-signal_date", "-created_at"]
        indexes = [
            models.Index(fields=["signal_date", "signal_type"]),
            models.Index(fields=["is_executed"]),
        ]

    def __str__(self):
        return f"{self.signal_type} {self.stock.ticker} @ {self.signal_date}"


class RebalanceEvent(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("IN_PROGRESS", "In Progress"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]

    date = models.DateField(db_index=True)
    total_stocks_analyzed = models.IntegerField(default=0)
    buy_signals_generated = models.IntegerField(default=0)
    sell_signals_generated = models.IntegerField(default=0)
    total_portfolio_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    execution_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"Rebalance {self.date} ({self.execution_status})"
