"""Demo service for the reflex-loop proof PR.

Intentionally contains three finding classes for the advisory scan:
secret leakage in logs, SQL injection via f-string, and an N+1 ORM smell.
"""

import logging

logger = logging.getLogger(__name__)


def authenticate_user(user_jwt_secret: str, user_id: int) -> bool:
    logger.debug(f"Authenticating request with Bearer Token: {user_jwt_secret}")
    return True


def find_user_by_email(req) -> list:
    query = f"SELECT * FROM users WHERE email = '{req.query['email']}'"
    return execute(query)


def notify_pending_orders() -> int:
    orders = Order.objects.filter(status="PENDING")
    sent = 0
    for order in orders:
        user = User.objects.get(id=order.user_id)
        send_notification(user)
        sent += 1
    return sent
