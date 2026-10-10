from django.core.management.base import BaseCommand
from trading.services.massive_client import MassiveAPIClient
from datetime import date

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MassiveAPIClient()
        result = client.fetch_bulk_momentum_data(["AAPL", "NVDA"], date(2026, 5, 10))
        print(result)