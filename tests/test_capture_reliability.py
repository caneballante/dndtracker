import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import capture_reliability as reliability
import live_player_assistant
import server


class FakeAwake:
    def __init__(self, *, supported=True, fail=False):
        self.supported = supported
        self.active = False
        self.error = ""
        self.fail = fail
        self.acquires = 0
        self.releases = 0

    def acquire(self):
        self.acquires += 1
        if self.fail:
            self.error = "power request denied"
        else:
            self.active = True
        return self.status()

    def release(self):
        self.releases += 1
        self.active = False
        return self.status()

    def status(self):
        return {"supported": self.supported, "active": self.active, "error": self.error}


class CaptureLeaseTests(unittest.TestCase):
    def test_start_renew_stop_and_shutdown_manage_one_temporary_request(self):
        backend = FakeAwake()
        clock = [10.0]
        manager = reliability.CaptureLeaseManager(
            backend, timeout=45, reaper_interval=999, time_fn=lambda: clock[0]
        )
        self.addCleanup(manager.shutdown)
        self.assertTrue(manager.acquire_or_renew("12345678", now=10)["active"])
        manager.acquire_or_renew("12345678", now=20)
        clock[0] = 20
        self.assertEqual(1, backend.acquires)
        self.assertEqual(65, manager.status("12345678")["leaseExpiresAt"])
        self.assertFalse(manager.release("12345678")["active"])
        self.assertEqual(1, backend.releases)
        manager.acquire_or_renew("87654321", now=30)
        manager.shutdown()
        self.assertFalse(backend.active)

    def test_abandoned_lease_expires_and_reports_callback(self):
        backend = FakeAwake()
        expired = []
        manager = reliability.CaptureLeaseManager(
            backend, timeout=45, reaper_interval=999,
            on_expire=lambda sid, heartbeat, now: expired.append((sid, heartbeat, now)),
        )
        self.addCleanup(manager.shutdown)
        manager.acquire_or_renew("12345678", now=10)
        self.assertEqual(["12345678"], manager.expire_stale(now=56))
        self.assertEqual([("12345678", 10.0, 56.0)], expired)
        self.assertFalse(backend.active)

    def test_power_request_failure_and_unsupported_platform_are_safe(self):
        failed = reliability.CaptureLeaseManager(FakeAwake(fail=True), reaper_interval=999)
        unsupported = reliability.CaptureLeaseManager(
            reliability.UnsupportedSystemAwakeRequest(), reaper_interval=999
        )
        self.addCleanup(failed.shutdown)
        self.addCleanup(unsupported.shutdown)
        self.assertFalse(failed.acquire_or_renew("12345678")["active"])
        self.assertIn("denied", failed.status("12345678")["error"])
        self.assertFalse(unsupported.acquire_or_renew("87654321")["supported"])


class CaptureServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.uploads = Path(self.temp.name) / "uploads"
        self.sid = "12345678"
        self.session = self.uploads / self.sid
        self.session.mkdir(parents=True)
        (self.session / "status.json").write_text(json.dumps({
            "sessionId": self.sid,
            "chunks": [],
        }), encoding="utf-8")
        self.backend = FakeAwake()
        self.manager = reliability.CaptureLeaseManager(self.backend, timeout=45, reaper_interval=999)
        self.patchers = [
            mock.patch.object(server, "UPLOADS_DIR", str(self.uploads)),
            mock.patch.object(server, "CAPTURE_LEASES", self.manager),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.manager.shutdown)
        self.manager.on_expire = server._capture_lease_expired

    def read_status(self):
        return json.loads((self.session / "status.json").read_text(encoding="utf-8"))

    def audit(self):
        path = self.session / reliability.CAPTURE_AUDIT_FILENAME
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_stabilization_diagnostics_keep_only_metadata_and_preserve_capture(self):
        server._capture_start(self.sid, {"recorderState": "recording", "trackState": "live", "streamActive": True})
        for event in ("recorder_rollover_started", "capture_recovery_stage", "meter_started",
                      "meter_context_state", "meter_close_failed", "meter_error"):
            result = server._capture_materialize_event(self.sid, event, {
                "stage": "recorder_started", "attemptId": 2, "generation": 3,
                "contextState": "running", "timeoutMs": 10000, "chunkIndex": 4,
                "rawAudio": "synthetic private bytes", "transcript": "synthetic private content",
                "apiKey": "synthetic secret", "campaignContent": "synthetic private context",
            })
            self.assertEqual("healthy", result["state"])
            details = self.audit()[-1]["details"]
            self.assertEqual(2, details["attemptId"])
            self.assertNotIn("rawAudio", details)
            self.assertNotIn("transcript", details)
            self.assertNotIn("apiKey", details)
            self.assertNotIn("campaignContent", details)
        for event in ("capture_recovery_failed", "capture_recovery_cancelled"):
            result = server._capture_materialize_event(self.sid, event, {"stage": "microphone_reconnecting", "reason": "synthetic cancellation"})
            self.assertEqual("interrupted", result["state"])
            self.assertTrue(result["recoveryRequired"])

    def test_start_heartbeat_gap_recovery_and_stop_are_durable(self):
        started = server._capture_start(self.sid, {
            "recorderState": "recording", "trackState": "live", "streamActive": True,
        })
        self.assertEqual("healthy", started["state"])
        self.assertTrue(started["keepAwake"]["active"])
        heartbeat = server._capture_heartbeat(self.sid, {
            "clientState": "healthy", "recorderState": "recording",
            "trackState": "live", "streamActive": True,
        })
        self.assertEqual("healthy", heartbeat["state"])
        interrupted = server._capture_materialize_event(self.sid, "capture_gap", {
            "gapId": "gap_1", "gapStartedAt": 100, "gapEndedAt": 170,
            "durationSeconds": 70, "reason": "sleep_gap",
        })
        self.assertTrue(interrupted["partialCapture"])
        recovered = server._capture_materialize_event(self.sid, "capture_recovered", {
            "gapId": "gap_1", "gapStartedAt": 100, "gapEndedAt": 180,
            "durationSeconds": 80, "reason": "sleep_gap", "recovered": True,
        })
        self.assertEqual("recovered_with_gap", recovered["state"])
        self.assertTrue(recovered["gaps"][0]["recovered"])
        finalizing = server._capture_begin_finalization(self.sid, 0)
        self.assertEqual("finalizing", finalizing["state"])
        self.assertFalse(self.backend.active)
        stopped = server._capture_finish_finalization(
            self.sid, {"state": "ready_for_reconciliation", "finalExpectedChunkIndex": 0}
        )
        self.assertEqual("stopped", stopped["state"])
        self.assertTrue(stopped["partialCapture"])
        self.assertTrue(stopped["recoveredWithGap"])
        events = [item["event"] for item in self.audit()]
        self.assertIn("keep_awake_acquired", events)
        self.assertIn("capture_gap", events)
        self.assertIn("capture_recovered", events)
        self.assertIn("finalize_completed", events)

    def test_lease_expiry_marks_connectivity_unknown_without_inventing_audio_gap(self):
        server._capture_start(self.sid, {"recorderState": "recording", "trackState": "live"})
        heartbeat = self.manager.status(self.sid)["lastHeartbeatAt"]
        self.manager.expire_stale(now=heartbeat + 46)
        capture = self.read_status()["capture"]
        self.assertEqual("suspect", capture["state"])
        self.assertFalse(capture["partialCapture"])
        self.assertEqual([], capture["gaps"])
        server._capture_lease_expired(self.sid, heartbeat, heartbeat + 47)
        self.assertEqual(1, sum(item['event'] == 'heartbeat_expired' for item in self.audit()))
        resumed = server._capture_heartbeat(self.sid, {'clientState': 'healthy', 'recorderState': 'recording', 'trackState': 'live'})
        self.assertEqual('healthy', resumed['state'])
        self.assertFalse(resumed['connectionLost'])
        self.assertIn("heartbeat_expired", [item["event"] for item in self.audit()])

    def test_failed_finalization_remains_recoverable_and_legacy_session_is_readable(self):
        legacy = server._capture_status_response(self.sid)
        self.assertEqual("stopped", legacy["state"])
        server._capture_start(self.sid, {"recorderState": "recording", "trackState": "live"})
        server._capture_begin_finalization(self.sid, 0)
        failed = server._capture_finish_finalization(self.sid, error=RuntimeError("simulated"))
        self.assertEqual("error", failed["state"])
        self.assertTrue(failed["pendingFinalization"]["recoverable"])
        listed = server.list_sessions()[0]
        self.assertEqual("error", listed["captureStatus"])

    def test_partial_capture_requires_explicit_billable_confirmation(self):
        status = self.read_status()
        status["capture"] = reliability.normalize_capture_status({
            "state": "stopped", "partialCapture": True,
            "gaps": [{"gapId": "g", "startedAt": 1, "endedAt": 2}],
        })
        status["finalization"] = {"state": "ready_for_reconciliation"}
        (self.session / "status.json").write_text(json.dumps(status), encoding="utf-8")
        with mock.patch.object(server, "_structured_reconciliation_enabled", return_value=True):
            with self.assertRaisesRegex(ValueError, "recording gap"):
                server.run_structured_reconciliation(self.sid, confirm=True, model_client=mock.Mock())

    def test_player_companion_interruption_short_circuits_and_audits_without_model(self):
        (self.session / "transcripts.jsonl").write_text(json.dumps({
            "chunkIndex": 0, "createdAt": 1000,
            "text": "The party discussed the gate with the guard for several minutes."
        }) + "\n", encoding="utf-8")
        model = mock.Mock(side_effect=AssertionError("model must not run"))
        result = live_player_assistant.missed(
            self.sid, str(self.session), 5, enabled=True, budget=1.0,
            pricing_path="unused.json", model_client=model,
            reference_factory=lambda: ({}, lambda **_: {}, []),
            capture_status={"sessionOpen": True, "state": "interrupted", "partialCapture": True},
        )
        self.assertEqual("capture_interrupted", result["state"])
        self.assertEqual(0, result["newSpend"])
        model.assert_not_called()
        rows = [json.loads(line) for line in (self.session / live_player_assistant.AUDIT_FILE).read_text().splitlines()]
        self.assertEqual(["request_received", "capture_interrupted"], [row["state"] for row in rows])


if __name__ == "__main__":
    unittest.main()
