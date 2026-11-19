"""A Python draft's RPKI CA, with a ROA added in its Preview, is not offered by Keep."""

import httpx2
import pytest

from mniplab.labprocess import LabChild
from mniplab.tests.authoring_fixtures import AuthoringAPIFixture
from mniplab.tests.labs import authored_rpki_test_lab
from mniplab.webserver import registry


@pytest.mark.integration
class PythonRPKIKeepLabTests(AuthoringAPIFixture):
    """Keep of a Python Preview reviews what the script declares; RPKI objects change in the script."""

    def test_a_roa_added_in_a_python_preview_is_live_but_not_offered_by_keep(self) -> None:
        self._launch_with("multi", LabChild.launch)

        async def exercise(client: httpx2.AsyncClient) -> None:
            drafted = (await client.post("/api/authoring/drafts", json={})).json()
            drafted["address_family"] = "ipv4"
            drafted["catalog"]["name"] = "RPKI python keep"
            drafted["catalog"]["area"] = "DNS"
            drafted["topology"] = authored_rpki_test_lab()
            saved = await client.put(f"/api/authoring/drafts/{drafted['id']}",
                                     json={"revision": drafted["revision"], "document": drafted})
            self.assertEqual(saved.status_code, 200, saved.text)
            source = (await client.get(f"/api/authoring/drafts/{drafted['id']}/python")).json()["source"]

            uploaded = await client.post("/api/authoring/drafts/upload", files={
                "file": ("rpki_python_keep.py", source, "text/x-python")})
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            draft = uploaded.json()
            key = None
            try:
                started = await client.post(f"/api/authoring/drafts/{draft['id']}/preview",
                                            json={"revision": draft["revision"]})
                self.assertEqual(started.status_code, 200, started.text)
                key = started.json()["key"]
                handle = registry.lab_service.get(key).handle
                added = {"prefix": "10.3.0.0/24", "max_length": 24, "origin": 65003}
                handle.add_rpki_roa("ca", 65003, added)
                self.assertIn(added["prefix"], [
                    roa["prefix"] for item in handle.list_rpki_cas("ca")
                    if item["asn"] == 65003 for roa in item["roas"]])

                review = await client.get(f"/api/authoring/drafts/{draft['id']}/keep")
                self.assertEqual(review.status_code, 200, review.text)
                self.assertNotIn("rpki_objects", [item["kind"] for item in review.json()["features"]])
            finally:
                if key:
                    await client.post(f"/api/labs/{key}/stop")

        self._as("instructor", exercise)
