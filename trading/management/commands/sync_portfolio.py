from django.core.management.base import BaseCommand, CommandError

from portfolio.models import Portfolio
from trading.services.snaptrade_client import get_trading_executor


class Command(BaseCommand):
    help = "Sync positions and cash for a portfolio from SnapTrade."

    def add_arguments(self, parser):
        parser.add_argument(
            "portfolio",
            help="Portfolio name (or numeric id) to sync.",
        )

    def handle(self, *args, **options):
        identifier = options["portfolio"]
        query = {"pk": identifier} if identifier.isdigit() else {"name": identifier}
        try:
            portfolio = Portfolio.objects.get(**query)
        except Portfolio.DoesNotExist:
            raise CommandError(f"Portfolio '{identifier}' does not exist")

        try:
            positions = get_trading_executor().sync_portfolio_positions(portfolio)
        except Exception as e:  # noqa: BLE001 - report cleanly to the console
            raise CommandError(f"Sync failed: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {len(positions)} positions for '{portfolio.name}'."
            )
        )
