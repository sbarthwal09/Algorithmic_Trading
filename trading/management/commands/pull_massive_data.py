from datetime import date, datetime

from django.core.management.base import BaseCommand

from trading.services.massive_client import get_massive_client


class Command(BaseCommand):
    help = "Fetch bulk momentum prices from Massive for the given tickers."

    def add_arguments(self, parser):
        parser.add_argument("tickers", nargs="+", help="Ticker symbols, e.g. AAPL NVDA")
        parser.add_argument(
            "--date",
            default=None,
            help="Calculation date in YYYY-MM-DD format (defaults to today).",
        )

    def handle(self, *args, **options):
        if options["date"]:
            calculation_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
        else:
            calculation_date = date.today()

        client = get_massive_client()
        result = client.fetch_bulk_momentum_data(
            options["tickers"], calculation_date
        )

        for ticker, data in result.items():
            self.stdout.write(
                f"{ticker}: 12m={data['price_12m']} 1m={data['price_1m']}"
            )
