from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from portfolio.models import Portfolio
from trading.services.strategy_engine import get_strategy_engine


class Command(BaseCommand):
    help = "Run a momentum rebalance for a portfolio."

    def add_arguments(self, parser):
        parser.add_argument(
            "portfolio",
            help="Portfolio name (or numeric id) to rebalance.",
        )
        parser.add_argument(
            "--date",
            default=None,
            help="Rebalance date in YYYY-MM-DD format (defaults to today).",
        )

    def handle(self, *args, **options):
        identifier = options["portfolio"]
        query = {"pk": identifier} if identifier.isdigit() else {"name": identifier}
        try:
            portfolio = Portfolio.objects.get(**query)
        except Portfolio.DoesNotExist:
            raise CommandError(f"Portfolio '{identifier}' does not exist")

        if options["date"]:
            calculation_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
        else:
            calculation_date = date.today()

        event = get_strategy_engine(portfolio).execute_rebalance(calculation_date)

        if event.execution_status == "COMPLETED":
            self.stdout.write(
                self.style.SUCCESS(
                    f"Rebalance completed: {event.total_stocks_analyzed} analysed, "
                    f"{event.buy_signals_generated} buys, "
                    f"{event.sell_signals_generated} sells."
                )
            )
        else:
            raise CommandError(f"Rebalance failed: {event.error_message}")
