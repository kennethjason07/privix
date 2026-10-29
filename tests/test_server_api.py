"""Unit and integration tests for Privix FastAPI server endpoints."""

import unittest
from fastapi.testclient import TestClient
from privix_app.server import app, _tasks, TaskRecord
from privix_app.task_state import TaskState


class TestPrivixServerAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_get_root_serves_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Privix", resp.text)

    def test_get_status_endpoint(self):
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("ollama", data)
        self.assertIn("ollama_model", data)

    def test_get_audit_endpoint(self):
        resp = self.client.get("/api/audit")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("entries", data)
        self.assertIsInstance(data["entries"], list)

    def test_mock_status_and_reset(self):
        resp = self.client.get("/api/mock-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("submissions", data)
        self.assertIn("portal_url", data)

        reset_resp = self.client.post("/api/reset")
        self.assertEqual(reset_resp.status_code, 200)
        self.assertEqual(reset_resp.json()["submissions"], 0)

    def test_approve_nonexistent_task(self):
        resp = self.client.post("/api/approve", json={"task_id": "nonexistent", "approved": True})
        self.assertEqual(resp.status_code, 404)

    def test_cancel_requires_task_id(self):
        resp = self.client.post("/api/cancel", json={})
        self.assertEqual(resp.status_code, 400)

    def test_cancel_non_active_task(self):
        resp = self.client.post("/api/cancel", json={"task_id": "none_123"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "not_active")

    def test_invalid_run_mode(self):
        resp = self.client.post("/api/run", json={"mode": "invalid_mode"})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
