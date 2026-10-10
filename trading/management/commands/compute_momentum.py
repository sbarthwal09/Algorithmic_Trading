from datetime import date, datetime

from django.core.management.base import BaseCommand

from trading.services.momentum_calculator import get_momentum_calculator


class Command(BaseCommand):
    help = "Compute and rank momentum scores for the active stock universe."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            default=None,
            help="Calculation date in YYYY-MM-DD format (defaults to today).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process the first N stocks in the universe.",
        )

    def handle(self, *args, **options):
        if options["date"]:
            calculation_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
        else:
            calculation_date = date.today()

        calculator = get_momentum_calculator()
        stocks = calculator.update_stock_universe()
        if options["limit"]:
            stocks = stocks[: options["limit"]]

        scores = calculator.calculate_momentum_scores_bulk(stocks, calculation_date)
        calculator.rank_stocks_by_momentum(calculation_date)

        self.stdout.write(
            self.style.SUCCESS(
                f"Calculated {len(scores)} momentum scores for {calculation_date}."
            )
        )
