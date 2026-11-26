import json
import unittest
from io import BytesIO

from atlaspm.data.disclosure_documents import document_url, VisibleText
from atlaspm.research.local_disclosure_ai import analyze


class DisclosureDocumentTests(unittest.TestCase):
    def test_sec_document_route(self):
        provider, url = document_url("sec", {"accession": "0000320193-24-000001",
                                             "archive_cik": "0000320193"})
        self.assertEqual(provider, "sec_document")
        self.assertTrue(url.endswith("/320193/000032019324000001/0000320193-24-000001.txt"))

    def test_fed_host_blocked(self):
        with self.assertRaises(ValueError):
            document_url("fed", {"url": "https://example.com/newsevents/pressreleases/a.htm"})

    def test_extraction(self):
        parser = VisibleText()
        parser.feed("<p>Revenue rose</p><script>hidden</script><p>Debt fell</p>")
        self.assertEqual(parser.parts, ["Revenue rose", "Debt fell"])

    def test_local_ai_evidence(self):
        findings = {"findings": [{"evidence_id": "document", "quote": "Revenue rose",
                                  "interpretation": "Reported revenue increase"}]}
        response = {"model": "local:latest", "done": True, "response": json.dumps(findings)}
        def opener(request, timeout):
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
            return BytesIO(json.dumps(response).encode())
        result = analyze({"text": "Revenue rose", "sha256": "a"*64}, "local:latest", opener=opener)
        self.assertFalse(result["live_execution_enabled"])
        self.assertEqual(result["findings"], findings["findings"])
        findings["findings"][0]["quote"] = "Invented quote"
        response["response"] = json.dumps(findings)
        with self.assertRaises(ValueError):
            analyze({"text": "Revenue rose", "sha256": "a"*64}, "local:latest", opener=opener)
