"""SnapTrade brokerage integration.

Wraps the SnapTrade Python SDK (v13+) to sync positions, place orders and
manage the connection flow. All network access goes through
:class:`TradingExecutor`.
"""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from django.conf import settings

from portfolio.models import Portfolio, Position, Trade
from trading.models import Stock

logger = logging.getLogger(__name__)

try:
    from snaptrade_client import SnapTrade, SnapTradeAuth
except ImportError:  # pragma: no cover - optional dependency
    SnapTrade = None
    SnapTradeAuth = None


def _to_decimal(value) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


class TradingExecutor:
    def __init__(self):
        client_id = getattr(settings, "SNAPTRADE_CLIENT_ID", "")
        consumer_key = getattr(settings, "SNAPTRADE_CONSUMER_KEY", "")

        if SnapTrade is None:
            logger.warning("snaptrade-python-sdk is not installed; trading disabled")
            self.snaptrade = None
        elif not client_id or not consumer_key:
            logger.warning("SnapTrade credentials are not configured; trading disabled")
            self.snaptrade = None
        else:
            auth = SnapTradeAuth.personal_api_key(
                consumer_key=consumer_key, client_id=client_id
            )
            self.snaptrade = SnapTrade(auth=auth)

    def _require_client(self):
        if self.snaptrade is None:
            raise RuntimeError(
                "SnapTrade is not configured. Set SNAPTRADE_CLIENT_ID and "
                "SNAPTRADE_CONSUMER_KEY (SNAPETRADE_CLIENT_SECRET) in your .env."
            )
        return self.snaptrade

    @staticmethod
    def _resolve_secret(portfolio: Portfolio, user_secret: str = None) -> str:
        secret = user_secret or portfolio.snaptrade_user_secret
        if not secret:
            raise ValueError("SnapTrade user secret is required for this operation")
        return secret

    # -- Connection management ------------------------------------------------

    def register_user(self, portfolio: Portfolio) -> Portfolio:
        """Create (or fetch) a SnapTrade user for the portfolio."""
        client = self._require_client()
        user_id = portfolio.snaptrade_user_id or f"portfolio-{portfolio.pk}"

        response = client.authentication.register_snap_trade_user(user_id=user_id)
        body = response.body

        portfolio.snaptrade_user_id = body.get("userId", user_id)
        portfolio.snaptrade_user_secret = body.get("userSecret", "")
        portfolio.save(update_fields=["snaptrade_user_id", "snaptrade_user_secret"])
        logger.info(f"Registered SnapTrade user {portfolio.snaptrade_user_id}")
        return portfolio

    def get_connection_url(self, portfolio: Portfolio) -> str:
        """Return the SnapTrade connection portal URL for the portfolio."""
        client = self._require_client()
        if not portfolio.snaptrade_user_id:
            self.register_user(portfolio)

        secret = self._resolve_secret(portfolio)
        response = client.authentication.login_snap_trade_user(
            user_id=portfolio.snaptrade_user_id,
            user_secret=secret,
            custom_redirect=settings.SNAPTRADE_REDIRECT_URI,
            connection_type="trade",
        )
        body = response.body
        return body.get("redirectURI") or body.get("redirectUri", "")

    # -- Position sync --------------------------------------------------------

    @staticmethod
    def _extract_ticker(pos_data: dict) -> Optional[str]:
        """Pull a ticker out of either the v2 (instrument) or legacy shape."""
        instrument = pos_data.get("instrument") or {}
        if isinstance(instrument, dict):
            symbol = instrument.get("symbol") or instrument.get("raw_symbol")
            if isinstance(symbol, str):
                return symbol

        legacy = pos_data.get("symbol") or {}
        if isinstance(legacy, dict):
            nested = legacy.get("symbol") or {}
            if isinstance(nested, dict):
                symbol = nested.get("raw_symbol") or nested.get("symbol")
                if isinstance(symbol, str):
                    return symbol
            symbol = legacy.get("raw_symbol") or legacy.get("symbol")
            if isinstance(symbol, str):
                return symbol
        return None

    def sync_portfolio_positions(
        self, portfolio: Portfolio, user_secret: str = None
    ) -> List[Position]:
        if not portfolio.snaptrade_user_id or not portfolio.snaptrade_account_id:
            raise ValueError("Portfolio must have SnapTrade user and account IDs")

        secret = self._resolve_secret(portfolio, user_secret)
        client = self._require_client()

        try:
            response = client.account_information.get_all_account_positions(
                user_id=portfolio.snaptrade_user_id,
                user_secret=secret,
                account_id=portfolio.snaptrade_account_id,
            )
            positions_data = response.body.get("results", [])

            synced_positions = []
            for pos_data in positions_data:
                ticker = self._extract_ticker(pos_data)
                if not ticker:
                    continue

                units = _to_decimal(pos_data.get("units")) or Decimal("0")
                if units <= 0:
                    continue

                price = _to_decimal(pos_data.get("price")) or Decimal("0")
                cost_basis = _to_decimal(pos_data.get("cost_basis"))
                average_cost = (
                    (cost_basis / units)
                    if cost_basis and units
                    else price
                )

                stock, _ = Stock.objects.get_or_create(
                    ticker=ticker, defaults={"name": ticker, "is_active": True}
                )

                position, created = Position.objects.update_or_create(
                    portfolio=portfolio,
                    stock=stock,
                    defaults={
                        "quantity": int(units),
                        "average_cost": average_cost,
                        "current_price": price,
                    },
                )
                position.update_current_value()
                position.save()
                synced_positions.append(position)
                logger.info(
                    f"{'Created' if created else 'Updated'} position: "
                    f"{ticker} - {position.quantity} shares"
                )

            # Update the cash balance.
            balance_response = client.account_information.get_user_account_balance(
                user_id=portfolio.snaptrade_user_id,
                user_secret=secret,
                account_id=portfolio.snaptrade_account_id,
            )
            balance_data = balance_response.body
            if isinstance(balance_data, dict):
                balance_data = balance_data.get("results", [])
            if balance_data:
                cash_balance = _to_decimal(balance_data[0].get("cash")) or Decimal("0")
                portfolio.current_cash = cash_balance
                portfolio.calculate_total_value()
                portfolio.save()
                logger.info(f"Updated portfolio cash balance to ${cash_balance}")

            return synced_positions

        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error syncing portfolio positions: {str(e)}")
            raise

    # -- Order execution ------------------------------------------------------

    def execute_buy_orders(
        self,
        portfolio: Portfolio,
        buy_list: List[Stock],
        total_value: Decimal,
        user_secret: str = None,
    ) -> List[Trade]:
        if not buy_list:
            return []

        secret = self._resolve_secret(portfolio, user_secret)
        client = self._require_client()

        allocation_per_stock = total_value / len(buy_list)
        executed_trades = []

        for stock in buy_list:
            trade = Trade.objects.create(
                portfolio=portfolio,
                stock=stock,
                trade_type="BUY",
                quantity=0,
                order_value=allocation_per_stock,
                status="PENDING",
            )

            try:
                response = client.trading.place_force_order(
                    account_id=portfolio.snaptrade_account_id,
                    action="BUY",
                    order_type="Market",
                    time_in_force="Day",
                    symbol=stock.ticker,
                    notional_value=float(allocation_per_stock),
                    user_id=portfolio.snaptrade_user_id,
                    user_secret=secret,
                )
                order_result = getattr(response, "body", response) or {}

                trade.external_order_id = str(order_result.get("id", ""))
                trade.snaptrade_order_id = trade.external_order_id
                trade.status = "SUBMITTED"
                trade.submitted_at = datetime.now()
                trade.save()

                executed_trades.append(trade)
                logger.info(
                    f"Submitted buy order: ${float(allocation_per_stock)} of "
                    f"{stock.ticker}"
                )
            except Exception as e:  # noqa: BLE001 - surface broker errors
                trade.status = "REJECTED"
                trade.error_message = str(e)
                trade.save()
                logger.error(f"Error executing buy order for {stock.ticker}: {e}")
                raise Exception(f"Buy order failed for {stock.ticker}: {e}")

        return executed_trades

    def execute_sell_orders(
        self, portfolio: Portfolio, sell_list: List[Stock], user_secret: str = None
    ) -> List[Trade]:
        if not sell_list:
            return []

        secret = self._resolve_secret(portfolio, user_secret)
        client = self._require_client()

        executed_trades = []
        current_positions = {
            pos.stock.ticker: pos for pos in portfolio.get_current_positions()
        }

        for stock in sell_list:
            position = current_positions.get(stock.ticker)
            if position is None or position.quantity <= 0:
                continue

            trade = Trade.objects.create(
                portfolio=portfolio,
                stock=stock,
                trade_type="SELL",
                quantity=position.quantity,
                price=position.current_price,
                order_value=position.current_value,
                status="PENDING",
            )

            try:
                response = client.trading.place_force_order(
                    account_id=portfolio.snaptrade_account_id,
                    action="SELL",
                    order_type="Market",
                    time_in_force="Day",
                    symbol=stock.ticker,
                    units=float(position.quantity),
                    user_id=portfolio.snaptrade_user_id,
                    user_secret=secret,
                )
                order_result = getattr(response, "body", response) or {}

                trade.external_order_id = str(order_result.get("id", ""))
                trade.snaptrade_order_id = trade.external_order_id
                trade.status = "SUBMITTED"
                trade.submitted_at = datetime.now()
                trade.save()

                executed_trades.append(trade)
                logger.info(
                    f"Submitted sell order: {position.quantity} shares of "
                    f"{stock.ticker}"
                )
            except Exception as e:  # noqa: BLE001 - surface broker errors
                trade.status = "REJECTED"
                trade.error_message = str(e)
                trade.save()
                logger.error(f"Error executing sell order for {stock.ticker}: {e}")
                raise Exception(f"Sell order failed for {stock.ticker}: {e}")

        return executed_trades

    def update_trade_status(self, trade: Trade, user_secret: str = None) -> bool:
        if not trade.external_order_id or trade.status in [
            "FILLED",
            "CANCELLED",
            "REJECTED",
        ]:
            return False

        secret = self._resolve_secret(trade.portfolio, user_secret)
        client = self._require_client()

        status_map = {
            "EXECUTED": "FILLED",
            "FILLED": "FILLED",
            "PARTIALLY_EXECUTED": "PARTIALLY_FILLED",
            "CANCELED": "CANCELLED",
            "CANCELLED": "CANCELLED",
            "REJECTED": "REJECTED",
            "PENDING": "SUBMITTED",
            "OPEN": "SUBMITTED",
        }

        try:
            response = client.account_information.get_user_account_order_detail(
                brokerage_order_id=trade.external_order_id,
                account_id=trade.portfolio.snaptrade_account_id,
                user_id=trade.portfolio.snaptrade_user_id,
                user_secret=secret,
            )
            order = getattr(response, "body", response) or {}

            old_status = trade.status
            broker_status = str(order.get("status", "")).upper()
            trade.status = status_map.get(broker_status, old_status)

            filled_quantity = _to_decimal(order.get("filled_quantity"))
            if filled_quantity is not None:
                trade.filled_quantity = int(filled_quantity)

            filled_price = _to_decimal(order.get("execution_price"))
            if filled_price is not None:
                trade.filled_price = filled_price

            if trade.status == "FILLED" and old_status != "FILLED":
                trade.filled_at = datetime.now()
                trade.update_position()

            trade.save()

            if old_status != trade.status:
                logger.info(
                    f"Trade {trade.id} status updated: {old_status} -> {trade.status}"
                )
            return True

        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error updating trade status for trade {trade.id}: {e}")
            return False

    # -- Pricing / helpers ----------------------------------------------------

    def _get_current_stock_price(self, ticker: str) -> Optional[Decimal]:
        try:
            from trading.services.massive_client import get_massive_client

            price = get_massive_client().get_price_on_date(ticker, datetime.now())
            return _to_decimal(price)
        except (ValueError, TypeError) as e:
            logger.error(f"Error getting current price for {ticker}: {e}")
            return None

    def get_available_cash_for_trading(self, portfolio: Portfolio) -> Decimal:
        """Cash available to trade, keeping a 5% buffer in reserve."""
        return (portfolio.current_cash or Decimal("0")) * Decimal("0.95")


def get_trading_executor() -> TradingExecutor:
    return TradingExecutor()
