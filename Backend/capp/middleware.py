# capp/middleware.py

import time
import logging

from django.db.utils import OperationalError

logger = logging.getLogger(__name__)


class PostgresWakeUpMiddleware:
    """
    Railway puts the Postgres database to sleep after inactivity.
    If the first request catches a cold database, this middleware
    retries the request once, giving Postgres time to wake up.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except OperationalError:
            logger.warning("Database connection failed – retrying once in 2 seconds.")
            time.sleep(2)
            # The second attempt will work if Postgres has woken up.
            return self.get_response(request)
