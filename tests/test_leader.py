"""Regression tests for leader election.

Before the fix, a renewal exception was logged and ignored forever: _is_leader
stayed True on a lock whose TTL had expired, so a standby could legitimately
take over while this instance kept polling.
"""
import asyncio
import os
import unittest
from unittest import mock

from chainnokizuna.services import leader as leader_mod
from chainnokizuna.services.leader import LeaderElection


class _FlakyRedis:
    """Raises for the first N eval() calls, then behaves."""

    def __init__(self, fail_times):
        self.calls = 0
        self._fail_times = fail_times

    async def eval(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("connection reset")
        return 1

    async def set(self, *args, **kwargs):
        return 1

    async def aclose(self):
        return None


class _RenewalScenario(unittest.TestCase):
    """Time is compressed so the TTL expiry is reachable in a test."""

    TTL = 0.30
    INTERVAL = 0.10

    def setUp(self):
        self._orig_ttl = LeaderElection.TTL
        self._orig_interval = LeaderElection.RENEW_INTERVAL
        LeaderElection.TTL = self.TTL
        LeaderElection.RENEW_INTERVAL = self.INTERVAL

    def tearDown(self):
        LeaderElection.TTL = self._orig_ttl
        LeaderElection.RENEW_INTERVAL = self._orig_interval

    def _drive(self, fail_times, seconds):
        async def scenario():
            fake = _FlakyRedis(fail_times)
            with mock.patch.object(leader_mod, "get_vk", return_value=fake):
                election = LeaderElection(bot_id="instance-A")
                election._is_leader = True
                election._renew_task = asyncio.create_task(election._renew_loop())
                await asyncio.sleep(seconds)
                still_running = not election._renew_task.done()
                election._renew_task.cancel()
                return election.is_leader, still_running
        return asyncio.run(scenario())

    def test_sustained_outage_relinquishes_leadership(self):
        is_leader, _ = self._drive(fail_times=999, seconds=self.TTL * 2)
        self.assertFalse(is_leader, "leadership must be given up once the lock expires")

    def test_transient_blip_keeps_leadership(self):
        is_leader, still_running = self._drive(fail_times=1, seconds=self.TTL * 2)
        self.assertTrue(is_leader, "a single failure must not cost leadership")
        self.assertTrue(still_running)

    def test_healthy_leader_is_unaffected(self):
        is_leader, still_running = self._drive(fail_times=0, seconds=self.TTL)
        self.assertTrue(is_leader)
        self.assertTrue(still_running)

    def test_failure_counter_resets_after_a_success(self):
        # 3 failures then success, repeated: the elapsed clock must restart.
        is_leader, _ = self._drive(fail_times=3, seconds=self.TTL * 3)
        self.assertTrue(is_leader, "counter must reset once renewal succeeds again")

    def test_renewal_logs_failures_with_progress(self):
        with self.assertLogs(leader_mod.logger, level="ERROR") as captured:
            self._drive(fail_times=2, seconds=self.TTL * 2)
        joined = "\n".join(captured.output)
        self.assertIn("consecutive", joined)
        self.assertIn("until the lock expires", joined)


@unittest.skipUnless(
    os.environ.get("RUN_REDIS_TESTS") == "1",
    "set RUN_REDIS_TESTS=1 to run tests that need a live Redis",
)
class TestAgainstLiveRedis(unittest.TestCase):
    def test_only_one_instance_holds_the_lock(self):
        import redis.asyncio as redis

        async def scenario():
            client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
            with mock.patch.object(leader_mod, "get_vk", return_value=client):
                LeaderElection.TTL = 10
                LeaderElection.RENEW_INTERVAL = 0.2
                a = LeaderElection(bot_id="live-A")
                b = LeaderElection(bot_id="live-B")
                self.assertTrue(await a.acquire())
                self.assertFalse(await b.acquire(), "second instance must not acquire")
                await asyncio.sleep(0.9)  # several renewals
                self.assertTrue(a.is_leader, "leader must survive renewals")
                await a.release()
                self.assertTrue(await b.acquire(), "lock is free after release")
                await b.release()
            await client.aclose()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
