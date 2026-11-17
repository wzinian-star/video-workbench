import http.client
import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import server
from server import WorkbenchServer

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = WorkbenchServer(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        h = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        headers = headers or {}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        h.request(method, path, body, headers)
        r = h.getresponse()
        result = r.status, dict(r.getheaders()), r.read()
        h.close()
        return result

    def token(self):
        s, headers, b = self.request("GET", "/api/state")
        self.assertEqual(s, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        return {"X-Workbench-Token": json.loads(b)["token"]}

    def test_rejects_absent_token_bad_origin_and_host(self):
        data = {"title":"a", "url":"https://example.com/asset"}
        self.assertEqual(self.request("POST","/api/assets", data)[0], 403)
        self.assertEqual(self.request("POST","/api/assets", data, {**self.token(),"Origin":"https://example.com"})[0], 403)
        self.assertEqual(self.request("GET","/api/state", headers={"Host":"example.com"})[0], 403)

    def test_workflow_metadata_and_custom_create(self):
        token=self.token()
        body={"title":"文化短片试制","description":"先问再讲","details":"片头用一个现场问题，过审再扩写。"}
        self.assertEqual(self.request("POST","/api/workflows",body)[0],403)
        s,_,raw=self.request("GET","/api/state")
        base=next(w for w in json.loads(raw)["skills"] if w["id"]=="pencil-drawing")
        self.assertIn("片头",base["details"])
        self.assertTrue(base["requires_voice"])
        s,_,raw=self.request("POST","/api/workflows",body,token)
        self.assertEqual(s,201)
        created=json.loads(raw)
        self.assertEqual(created["source"],"custom")
        self.assertTrue(created["requires_voice"])
        self.assertTrue(created["id"].startswith("workflow-"))
        rows=json.loads(self.request("GET","/api/state")[2])["skills"]
        self.assertTrue(any(w["id"]==created["id"] and w["details"]==body["details"] for w in rows))
        self.assertTrue((self.root/"workflows.json").exists())
        self.assertEqual(self.request("POST","/api/workflows",body,token)[0],409)

    def test_external_reference_and_cards(self):
        data = {"title":"示例","url":"https://example.com/video","category":"收藏参考"}
        s, _, b = self.request("POST","/api/assets", data, self.token())
        self.assertEqual(s, 201)
        item = json.loads(b)
        self.assertEqual(item["kind"], "link")
        self.assertEqual(item["category"], "收藏参考")
        self.assertIsNone(item["file"])
        s, _, b = self.request("POST",f"/api/assets/{item['id']}/cards", {"segment":"00:01–00:03","dimension":"镜头节奏","observation":"开门","application":"第一帧有人物","verification":"仅画面"}, self.token())
        self.assertEqual(s, 201)
        self.assertEqual(json.loads(b)["cards"][0]["observation"], "开门")
        self.assertTrue((self.root/"catalog.json").is_file())
        self.assertEqual(self.request("POST","/api/assets", {"title":"a","url":"file:///a"}, self.token())[0], 400)

    def test_safe_range_and_missing_record(self):
        (self.root/"素材").mkdir()
        (self.root/"素材"/"t.mp4").write_bytes(b"0123456789")
        (self.root/"catalog.json").write_text(json.dumps([
            {"id":"t","file":"素材/t.mp4"},
            {"id":"escape","file":"../missing.mp4"}
        ]))
        s, headers, b = self.request("GET","/media/asset/t",headers={"Range":"bytes=2-4"})
        self.assertEqual(s, 206)
        self.assertEqual(headers["Content-Range"],"bytes 2-4/10")
        self.assertEqual(b,b"234")
        self.assertEqual(self.request("GET","/media/asset/t",headers={"Range":"bytes=11-"})[0],416)
        self.assertEqual(self.request("GET","/media/asset/escape")[0],404)

    def test_audio_endpoint_needs_bytes_and_supplies_stream_url(self):
        (self.root/"素材").mkdir()
        (self.root/"素材"/"t.mp4").write_bytes(b"input")
        (self.root/".reference_audio").mkdir()
        clip = self.root/".reference_audio"/"reference.wav"
        clip.write_bytes(b"audio")
        (self.root/"catalog.json").write_text(json.dumps([
            {"id":"t","file":"素材/t.mp4"},
            {"id":"link","kind":"link","file":None,"url":"https://example.com/video"}
        ]))
        fake = SimpleNamespace(
            analyze_reference=lambda root, key, source, segment, language: {"clip_id":"sample","segment":segment,"language":language},
            reference_clip_path=lambda root, key, clip_id: clip,
        )
        with patch.object(server, "audio_refs", fake):
            self.assertEqual(self.request("POST","/api/assets/link/audio",{},self.token())[0],400)
            s, _, b = self.request("POST","/api/assets/t/audio",{"segment":"00:01–00:03","language":"es"},self.token())
            self.assertEqual(s,200)
            obj = json.loads(b)
            self.assertEqual(obj["segment"],"00:01–00:03")
            self.assertEqual(obj["language"],"es")
            self.assertEqual(self.request("GET",obj["audio_url"],headers={"Range":"bytes=1-2"})[2],b"ud")

    def test_jobs_enter_sample_gate_and_media_uses_job_directory(self):
        (self.root/"任务").mkdir()
        clip=self.root/"任务"/"sample.mp4"
        clip.write_bytes(b"sample-h264")
        other=self.root/"outside.mp4"
        other.write_bytes(b"do not serve")
        starts=[]
        fake=SimpleNamespace(
            installed_skills=lambda: [],
            list_jobs=lambda root: [],
            start_job=lambda root, payload: starts.append(payload) or {"id":"demo", "status":"running"},
            approve_job=lambda root, job_id: {"id":job_id, "phase":"final"},
            media_path=lambda root, job_id, phase: other if phase=="final" else clip,
        )
        with patch.object(server,"jobs",fake):
            headers=self.token()
            self.assertEqual(self.request("POST","/api/jobs",{"mode":"direct"},headers)[0],400)
            self.assertFalse(starts)
            self.assertEqual(self.request("POST","/api/jobs",{"mode":"sample"},headers)[0],201)
            self.assertEqual(starts,[{"mode":"sample"}])
            self.assertEqual(self.request("POST","/api/jobs/demo/approve",{},headers)[0],200)
            self.assertEqual(self.request("GET","/media/job/demo/sample")[2],b"sample-h264")
            self.assertEqual(self.request("GET","/media/job/demo/final")[0],404)

    def test_multipart_may_preview_uploaded_video(self):
        boundary="sample-boundary"
        body=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nhello\r\n"
              f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"../test.mp4\"\r\nContent-Type: video/mp4\r\n\r\nmedia\r\n"
              f"--{boundary}--\r\n").encode()
        s, _, b = self.request("POST","/api/assets",body,{**self.token(),"Content-Type":f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(s, 201)
        item=json.loads(b)
        self.assertEqual(item["kind"],"video")
        self.assertEqual(self.request("GET",item["media"])[2],b"media")

if __name__ == "__main__":
    unittest.main()
