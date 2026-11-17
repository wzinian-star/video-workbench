import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_refs import AudioReferenceError, analyze_reference, reference_clip_path, _transcribe


def ffargs(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg needed")
class AudioReferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "tone.wav"
        ffargs("-f", "lavfi", "-i", "sine=frequency=180:duration=1",
               "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.8",
               "-f", "lavfi", "-i", "sine=frequency=215:duration=1",
               "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]", "-map", "[a]", str(self.source))

    @patch("audio_refs._whisper_setup", return_value=None)
    def test_clip_levels_pause_and_pitch_use_original_time(self, _):
        report = analyze_reference(self.root, "fixture", self.source, "00:00.40–00:02.60")
        self.assertEqual(report["segment_start_sec"], 0.4)
        self.assertEqual(report["duration_sec"], 2.2)
        self.assertAlmostEqual(report["pauses"][0]["start_sec"], 1.0, delta=0.1)
        self.assertAlmostEqual(report["pauses"][0]["end_sec"], 1.8, delta=0.1)
        self.assertTrue(any(abs(p["hz"] - 180) < 4 and p["time_sec"] < 1.0 for p in report["pitch_estimates"]))
        self.assertTrue(any(abs(p["hz"] - 215) < 4 and p["time_sec"] > 1.8 for p in report["pitch_estimates"]))
        self.assertLess(report["rms_dbfs"], report["peak_dbfs"])
        self.assertEqual(report["transcript"]["status"], "unavailable")
        clip = reference_clip_path(self.root, "fixture", report["clip_id"])
        self.assertTrue(clip.is_file())
        self.assertEqual(json.loads(clip.with_suffix(".json").read_text())["clip_id"], report["clip_id"])

    def test_approved_preview_works(self):
        approved = Path.home()/".codex/skills/pencil-drawing/assets/approved-adios-opening.mp4"
        if not approved.is_file():
            self.skipTest("local approved preview absent")
        shutil.copyfile(approved, self.root/"approved.mp4")
        report = analyze_reference(self.root, "approved", self.root/"approved.mp4", "0-6")
        self.assertEqual(report["duration_sec"], 6.0)
        self.assertGreater(len(report["pitch_estimates"]), 2)
        self.assertGreater(reference_clip_path(self.root, "approved", report["clip_id"]).stat().st_size, 1000)

    def test_reject_path_traversal_and_invalid_range(self):
        for identifier in ["../bad", "/tmp"]:
            with self.assertRaises(AudioReferenceError):
                analyze_reference(self.root, identifier, self.source, None)
        for span in ["2-1", "0-10", "garbage", "NaN-2"]:
            with self.assertRaises(AudioReferenceError):
                analyze_reference(self.root, "fixture", self.source, span)
        with self.assertRaises(AudioReferenceError):
            reference_clip_path(self.root, "fixture", "../anything.mp3")

    def test_transcription_adapter_preserves_original_offsets(self):
        def fake_runner(cmd, timeout):
            if cmd[0] == "whisper-cli":
                self.assertIn("auto", cmd)
                self.assertNotIn("--translate", cmd)
                out = Path(cmd[cmd.index("-of") + 1]).with_suffix(".json")
                out.write_text(json.dumps({"transcription": [
                    {"offsets": {"from": 160, "to": 1140}, "text": " 你会说 adiós？ "}
                ]}))
        with patch("audio_refs._run", side_effect=fake_runner):
            report = _transcribe(("whisper-cli", self.root/"ggml-small.bin"),
                                 "ffmpeg", self.source, 3.5)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["segments"], [
            {"start_sec": 3.66, "end_sec": 4.64, "text": "你会说 adiós？"}
        ])

    def test_no_audio_track(self):
        source = self.root / "silent.mp4"
        ffargs("-f", "lavfi", "-i", "color=c=black:s=32x32:r=2:d=1", str(source))
        with self.assertRaisesRegex(AudioReferenceError, "没有音轨"):
            analyze_reference(self.root, "silent", source)


if __name__ == "__main__":
    unittest.main()
