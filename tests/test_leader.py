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


class TestRenewalLoggingIsThrottled(unittest.TestCase):
    """A Redis outage must not emit one error line per renewal attempt, per instance.

    RENEW_INTERVAL is 5s, so an unthrottled loop logs once per attempt for as
    long as the outage lasts, on every instance. The give-up path must still
    log, and the first few failures must still be visible.
    """

    MAX_ATTEMPTS = 50

    def _run_with_failing_redis(self, attempts):
        """Drive _renew_loop for a fixed number of failing attempts."""
        class _Stop(Exception):
            pass

        election = LeaderElection(bot_id="throttle")
        election._is_leader = True
        election.TTL = 10_000  # effectively never expires, so only throttling applies

        async def scenario():
            redis = mock.AsyncMock()
            redis.eval.side_effect = RuntimeError("connection reset")
            seen = []

            async def fake_sleep(_delay):
                seen.append(1)
                if len(seen) >= attempts:
                    raise _Stop

            with mock.patch.object(leader_mod, "get_vk", return_value=redis), \
                 mock.patch.object(leader_mod.asyncio, "sleep", new=fake_sleep):
                await leader_mod.LeaderElection._renew_loop(election)
            return seen

        with self.assertLogs(leader_mod.logger, level="ERROR") as captured:
            with self.assertRaises(_Stop):
                asyncio.run(scenario())
        return captured.output

    def test_logging_is_throttled_below_one_line_per_attempt(self):
        lines = self._run_with_failing_redis(self.MAX_ATTEMPTS)
        self.assertLessEqual(
            len(lines), 8,
            f"{self.MAX_ATTEMPTS} attempts produced {len(lines)} log lines; expected throttling",
        )

    def test_first_failures_are_still_logged(self):
        # The loop sleeps before evaluating, so N sleeps yield N-1 logged
        # failures; what matters is that the early ones are not suppressed.
        lines = self._run_with_failing_redis(4)
        self.assertGreaterEqual(len(lines), 3, "the first few failures must remain visible")
        self.assertTrue(
            all("Error renewing leadership" in line for line in lines),
            f"expected only throttle-path lines, got {lines}",
        )

    def test_giving_up_still_logs_exactly_once(self):
        class _Stop(Exception):
            pass

        election = LeaderElection(bot_id="give-up")
        election._is_leader = True
        election.TTL = 0  # any failure exceeds the TTL, so it gives up at once

        async def scenario():
            redis = mock.AsyncMock()
            redis.eval.side_effect = RuntimeError("connection reset")
            with mock.patch.object(leader_mod, "get_vk", return_value=redis), \
                 mock.patch.object(leader_mod.asyncio, "sleep", new=mock.AsyncMock()):
                await leader_mod.LeaderElection._renew_loop(election)

        with self.assertLogs(leader_mod.logger, level="ERROR") as captured:
            asyncio.run(scenario())

        self.assertEqual(len(captured.output), 1, "losing leadership must log once")
        self.assertIn("Lost leadership", captured.output[0])
        self.assertFalse(election.is_leader, "must stop claiming leadership")


if __name__ == "__main__":
    unittest.main()
