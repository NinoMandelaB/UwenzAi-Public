# capp/tests_cache_isolation.py
"""
Test isolation between the Django cache and raw Redis state.

``manage.py test --parallel`` runs several worker processes against one Redis
database, and many suites call ``cache.clear()`` in setUp/tearDown. With the
Redis cache backend that is a FLUSHDB, which also deleted the agent gateway's
locks (raw Redis keys) while another worker's test was holding them. That made
``test_key_concurrency_and_playground_account_lock`` fail intermittently: the
second request it expected to be refused found no lock and went through.

The test settings now keep the Django cache process-local, so ``cache.clear()``
can only affect the worker that calls it.
"""

import uuid

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase

from capp.classes.agent_gateway.guards import GatewayBusy, gateway_lock


class CacheClearIsolationTests(SimpleTestCase):
    def test_test_runs_use_a_process_local_cache(self):
        self.assertEqual(
            settings.CACHES["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )

    def test_cache_clear_does_not_release_a_held_gateway_lock(self):
        if not getattr(settings, "REDIS_URL", None):
            self.skipTest("gateway locks need REDIS_URL")

        identity = f"cache-isolation:{uuid.uuid4()}"
        with gateway_lock(identity):
            # What a test in another worker does in its setUp.
            cache.clear()

            with self.assertRaises(GatewayBusy):
                with gateway_lock(identity):
                    pass
