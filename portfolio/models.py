from decimal import Decimal

from django.db import models
from django.utils import timezone


class Portfolio(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    initial_cash = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    current_cash = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    snaptrade_user_id = models.CharField(max_length=100, blank=True)
    snaptrade_account_id = models.CharField(max_length=100, blank=True)
    snaptrade_user_secret = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "portfolios"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_connected(self) -> bool:
        """True when the portfolio is linked to a SnapTrade account."""
        return bool(
            self.snaptrade_user_id
            and self.snaptrade_account_id
            and self.snaptrade_user_secret
        )

    def get_current_positions(self):
        """Return only positions that currently hold shares."""
        return self.positions.filter(quantity__gt=0).select_related("stock")

    def positions_value(self) -> Decimal:
        """Total market value of all open positions."""
        return sum(
            (position.current_value for position in self.get_current_positions()),
            Decimal("0"),
        )

    def calculate_total_value(self) -> Decimal:
        """Recompute and store the portfolio's total value."""
        self.total_value = (self.current_cash or Decimal("0")) + self.positions_value()
        return self.total_value

    def record_performance_metric(self, date=None) -> "PerformanceMetric":
        """Snapshot the portfolio value for a given day (idempotent)."""
        date = date or timezone.now().date()
        cash = self.current_cash or Decimal("0")
        positions_value = self.positions_value()
        total_value = cash + positions_value

        metric, _ = PerformanceMetric.objects.update_or_create(
            portfolio=self,
            date=date,
            defaults={
                "total_value": total_value,
                "cash_value": cash,
                "positions_value": positions_value,
                "total_trades": self.trades.count(),
            },
        )
        return metric


class Position(models.Model):
    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="positions"
    )
    stock = models.ForeignKey("trading.Stock", on_delete=models.CASCADE)
    quantity = models.IntegerField(default=0)
    average_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    current_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    current_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    unrealized_pnl = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    unrealized_pnl_percent = models.DecimalField(
        max_digits=8, decimal_places=4, default=0
    )
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "positions"
        unique_together = ("portfolio", "stock")
        ordering = ["-current_value"]

    def __str__(self):
        return f"{self.stock.ticker} x{self.quantity}"

    def update_current_value(self) -> Decimal:
        """Recompute market value and unrealised P&L for the position."""
        price = self.current_price or self.average_cost or Decimal("0")
        quantity = Decimal(self.quantity or 0)
        cost_basis = quantity * (self.average_cost or Decimal("0"))

        self.current_value = quantity * price
        self.unrealized_pnl = self.current_value - cost_basis
        if cost_basis > 0:
            self.unrealized_pnl_percent = (
                self.unrealized_pnl / cost_basis
            ) * Decimal("100")
        else:
            self.unrealized_pnl_percent = Decimal("0")
        return self.current_value


class Trade(models.Model):
    TRADE_TYPES = [
        ("BUY", "Buy"),
        ("SELL", "Sell"),
    ]

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("SUBMITTED", "Submitted"),
        ("FILLED", "Filled"),
        ("PARTIALLY_FILLED", "Partially Filled"),
        ("CANCELLED", "Cancelled"),
        ("REJECTED", "Rejected"),
    ]

    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="trades"
    )
    stock = models.ForeignKey("trading.Stock", on_delete=models.CASCADE)
    trade_type = models.CharField(max_length=4, choices=TRADE_TYPES)
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    filled_quantity = models.IntegerField(default=0)
    filled_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    order_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    external_order_id = models.CharField(max_length=100, blank=True)
    snaptrade_order_id = models.CharField(max_length=100, blank=True)
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    error_message = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trades"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["portfolio", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.trade_type} {self.quantity} {self.stock.ticker} ({self.status})"

    def update_position(self):
        """Apply a filled trade to the related position and cash balance."""
        quantity = self.filled_quantity or 0
        price = self.filled_price or self.price
        if quantity <= 0 or price is None:
            return None

        position, _ = Position.objects.get_or_create(
            portfolio=self.portfolio,
            stock=self.stock,
        )

        if self.trade_type == "BUY":
            total_cost = (position.average_cost * position.quantity) + (price * quantity)
            new_quantity = position.quantity + quantity
            position.average_cost = (
                total_cost / new_quantity if new_quantity else Decimal("0")
            )
            position.quantity = new_quantity
            self.portfolio.current_cash -= price * quantity
        else:
            position.quantity = max(0, position.quantity - quantity)
            self.portfolio.current_cash += price * quantity

        position.current_price = price
        position.update_current_value()
        position.save()

        self.portfolio.calculate_total_value()
        self.portfolio.save()
        return position


class PerformanceMetric(models.Model):
    portfolio = models.ForeignKey(
        Portfolio, on_delete=models.CASCADE, related_name="performance_metrics"
    )
    date = models.DateField()
    total_value = models.DecimalField(max_digits=15, decimal_places=2)
    cash_value = models.DecimalField(max_digits=15, decimal_places=2)
    positions_value = models.DecimalField(max_digits=15, decimal_places=2)
    daily_return = models.DecimalField(
        max_digits=8, decimal_places=6, null=True, blank=True
    )
    cumulative_return = models.DecimalField(
        max_digits=8, decimal_places=6, null=True, blank=True
    )
    total_trades = models.IntegerField(default=0)
    winning_trades = models.IntegerField(default=0)
    losing_trades = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "performance_metrics"
        unique_together = ("portfolio", "date")
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["portfolio", "date"]),
        ]

    def __str__(self):
        return f"{self.portfolio.name} @ {self.date}"
