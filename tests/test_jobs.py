import json
import math
import shutil
import subprocess
import sys
import tempfile
import struct
import wave
import unittest
from pathlib import Path
from unittest.mock import patch

import jobs


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'selected.mp4').write_bytes(b'media')
        (self.root / 'other.mp4').write_bytes(b'media')
        with wave.open(str(self.root/'voice.wav'), 'wb') as w:
            w.setparams((1,2,8000,0,'NONE','not compressed'))
            w.writeframes(struct.pack('<800h', *[int(6000*math.sin(i)) for i in range(800)]))
        (self.root / 'catalog.json').write_text(json.dumps([
            {'id':'one','title':'选中','kind':'video','file':'selected.mp4','category':'自制'},
            {'id':'two','title':'不选','kind':'video','file':'other.mp4','category':'自制'},
            {'id':'voice-a','title':'实际声音','kind':'audio','file':'voice.wav','category':'自制'},
            {'id':'not-mine','title':'外部声源','kind':'audio','file':'voice.wav','category':'收藏参考'},
        ]))
        self.payload = {'title':'测试','script':'脚本','skill':'pencil-drawing','mode':'sample','voice_asset_id':'voice-a',
                        'refs':[{'id':'one','dimensions':['画法'],'segment':'','notes':'不要仿配音'}]}
        p = patch.object(jobs, 'installed_skills', return_value=[{'id':'pencil-drawing','title':'铅笔画'}])
        p.start()
        self.addCleanup(p.stop)

    def test_job_prompt_scopes_selected_sources(self):
        with patch.object(jobs, '_spawn') as spawn:
            job = jobs.start_job(self.root, self.payload)
        prompt = spawn.call_args.args[-1]
        self.assertIn(str(self.root / 'selected.mp4'), prompt)
        self.assertNotIn('other.mp4', prompt)
        self.assertIn(str(self.root/'voice.wav'), prompt)
        self.assertNotIn('sha256', prompt)
        self.assertIn('不要仿配音', prompt)
        self.assertEqual(job['status'], 'running')
        with self.assertRaises(jobs.JobError):
            jobs.approve_job(self.root, job['id'])

    def test_invalid_skill_mode_asset_and_dimension(self):
        for key, value in (
            ('mode','direct'),('skill','../bad'),('refs',[{'id':'missing','dimensions':['画法']}]),
            ('refs',[{'id':'one','dimensions':['任意命令']}]),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(jobs.JobError):
                jobs.start_job(self.root, dict(self.payload, **{key:value}))

    def test_voice_preflight_guards_before_job_creation(self):
        for supplied in ('','one','not-mine'):
            submission = dict(self.payload, voice_asset_id=supplied)
            with patch.object(jobs, '_spawn') as spawn, self.assertRaises(jobs.JobError) as error:
                jobs.start_job(self.root, submission)
            self.assertEqual(error.exception.status, 422)
            self.assertFalse((self.root/'任务').exists())
            spawn.assert_not_called()
        with wave.open(str(self.root/'voice.wav'), 'wb') as w:
            w.setparams((1,2,8000,0,'NONE','not compressed'))
            w.writeframes(b'\0'*1600)
        with self.assertRaises(jobs.JobError) as error:
            jobs.start_job(self.root, self.payload)
        self.assertEqual(error.exception.status, 422)
        self.assertFalse((self.root/'任务').exists())

    def test_custom_workflow_freezes_details_and_optional_base(self):
        custom = {'id':'custom-doc','title':'纪实短片','details':{'pace':'稳健'}, 'requires_voice':True}
        submission = dict(self.payload, workflow_id='custom-doc')
        with patch.object(jobs, '_resolve_custom', return_value=custom), patch.object(jobs, '_spawn') as spawn:
            job = jobs.start_job(self.root, submission)
        self.assertEqual(job['skill'], 'custom-doc')
        prompt = spawn.call_args.args[-1]
        self.assertIn('纪实短片', prompt)
        self.assertIn('稳健', prompt)
        self.assertNotIn('pencil-drawing', prompt)
        stored = json.loads((self.root/'任务'/job['id']/'request.json').read_text())
        self.assertEqual(stored['workflow']['details'], custom['details'])
        with patch.object(jobs, '_resolve_custom', return_value=custom), self.assertRaises(jobs.JobError) as error:
            jobs._validate(self.root, dict(submission, voice_asset_id=''))
        self.assertEqual(error.exception.status, 422)

    def test_bad_directory_rejected(self):
        with self.assertRaises(jobs.JobError):
            jobs.media_path(self.root, '../elsewhere', 'sample')

    def test_missing_output_not_complete_and_no_media(self):
        with patch.object(jobs, '_run_codex'), patch.object(jobs, '_spawn', side_effect=jobs._worker):
            job = jobs.start_job(self.root, self.payload)
        data = jobs.list_jobs(self.root)[0]
        self.assertEqual(data['status'], 'failed')
        self.assertNotIn('sample_url', data)
        self.assertIsNone(jobs.media_path(self.root, job['id'], 'sample'))

    def test_known_missing_voice_no_raw_model_message(self):
        def failed(staging, prompt):
            (staging/'render_status.json').write_text(
                '{"code":"missing_voice","sensitive":"do not surface"}')
            raise jobs.JobError('raw string should not surface')
        with patch.object(jobs, '_run_codex', side_effect=failed), patch.object(jobs, '_spawn', side_effect=jobs._worker):
            jobs.start_job(self.root, self.payload)
        job = jobs.list_jobs(self.root)[0]
        self.assertIn('指定配音未就绪', job['error'])
        self.assertNotIn('raw string', job['error'])
        self.assertNotIn('sensitive', job['error'])

    def test_card_selection_excludes_known_outside_ranges(self):
        raw = json.loads((self.root/'catalog.json').read_text())
        raw[0]['cards'] = [
            {'dimension':'画法','segment':'00:00–00:02','observation':'界外'},
            {'dimension':'画法','segment':'00:02–00:06','observation':'界内'},
        ]
        (self.root/'catalog.json').write_text(json.dumps(raw))
        self.payload['refs'][0]['segment'] = '00:02–00:04'
        refs = jobs._validate(self.root, self.payload)['refs']
        self.assertEqual([x['observation'] for x in refs[0]['reference_cards']], ['界内'])
        self.payload['refs'][0]['segment'] = '12:70–13:00'
        with self.assertRaises(jobs.JobError):
            jobs._validate(self.root, self.payload)

    def test_shutdown_does_not_stop_other_workbench(self):
        other = self.root/'other'
        a = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        b = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        try:
            with jobs._PROCESS_LOCK:
                jobs._PROCESSES[self.root] = {a}
                jobs._PROCESSES[other] = {b}
            jobs.shutdown(self.root)
            self.assertIsNotNone(a.poll())
            self.assertIsNone(b.poll())
        finally:
            for process in (a,b):
                if process.poll() is None:
                    jobs._stop_group(process)
            with jobs._PROCESS_LOCK:
                for key in (self.root, other):
                    jobs._PROCESSES.pop(key, None)
                    jobs._STOPPING.discard(key)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg needed')
    def test_storyboard_without_voice_can_play_but_never_approve(self):
        source = self.root/'animatic.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=64x64:rate=10:d=1',
                        '-an','-c:v','libx264',str(source)], check=True)
        def fake_codex(staging, prompt):
            self.assertIn('只做约 8–15 秒无声分镜', prompt)
            self.assertNotIn(str(self.root/'voice.wav'), prompt)
            shutil.copyfile(source, staging/'sample.mp4')
        payload = dict(self.payload, mode='storyboard', voice_asset_id='unavailable-and-unused')
        with patch.object(jobs, '_run_codex', side_effect=fake_codex), patch.object(jobs, '_spawn', side_effect=jobs._worker):
            result = jobs.start_job(self.root, payload)
        saved = jobs.list_jobs(self.root)[0]
        self.assertEqual((saved['phase'],saved['status']), ('storyboard','complete'))
        self.assertTrue(saved['sample_url'].endswith('/sample'))
        path = jobs.media_path(self.root, result['id'],'sample')
        self.assertIsNotNone(path)
        self.assertIsNone(jobs._av_streams(jobs._probe(path))['audio'])
        with self.assertRaises(jobs.JobError) as error:
            jobs.approve_job(self.root, result['id'])
        self.assertEqual(error.exception.status, 409)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg needed')
    def test_storyboard_turns_down_an_audio_track(self):
        source = self.root/'audio-with-video.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=64x64:rate=10:d=1',
                        '-f','lavfi','-i','sine=duration=1','-c:v','libx264','-c:a','aac',str(source)],check=True)
        with patch.object(jobs, '_run_codex', side_effect=lambda st, p:shutil.copyfile(source,st/'sample.mp4')), patch.object(jobs, '_spawn', side_effect=jobs._worker):
            result = jobs.start_job(self.root, dict(self.payload, mode='storyboard', voice_asset_id=''))
        saved = jobs.list_jobs(self.root)[0]
        self.assertEqual(saved['status'],'failed')
        self.assertNotIn('sample_url',saved)
        self.assertIsNone(jobs.media_path(self.root,result['id'],'sample'))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg needed')
    def test_real_av_and_exact_prefix_checks(self):
        p = self.root / 'clip.mp4'
        subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=size=64x64:rate=10:d=1','-f','lavfi','-i',
                        'sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac',str(p)],check=True)
        q = self.root / 'different.mp4'
        subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=c=red:size=64x64:rate=10:d=1','-f','lavfi','-i',
                        'sine=frequency=220:duration=1','-c:v','libx264','-c:a','aac',str(q)],check=True)
        self.assertTrue(jobs._has_av(p))
        timed = self.root/'timed-ref'
        timed.mkdir()
        result = jobs._stage_timed_refs(timed,{
            'refs':[{'kind':'video','local_path':str(p),'numeric_range':[.2,.6]}]})
        self.assertTrue(result['refs'][0]['excerpt_is_trimmed'])
        excerpt = Path(result['refs'][0]['local_path'])
        self.assertTrue(excerpt.is_relative_to(timed))
        self.assertLessEqual(float(jobs._probe(excerpt)['format']['duration']), .45)
        self.assertFalse(jobs._verify_prefix(p, q))
        self.assertFalse(jobs._verify_prefix(p, p))
        with patch.object(jobs, '_spawn') as spawn:
            job = jobs.start_job(self.root, self.payload)
        folder = self.root/'任务'/job['id']
        with patch.object(jobs, '_run_codex', side_effect=lambda staging, prompt: shutil.copyfile(p, staging/'sample.mp4')):
            jobs._worker(*spawn.call_args.args)
        self.assertEqual(jobs.list_jobs(self.root)[0]['status'], 'review')
        self.assertEqual(jobs.media_path(self.root, job['id'], 'sample'), folder/'sample.mp4')
        sample_before = (folder/'sample.mp4').read_bytes()
        with patch.object(jobs, '_spawn') as full_spawn:
            full = jobs.approve_job(self.root, job['id'])
        self.assertEqual(full['phase'], 'final')
        with patch.object(jobs, '_run_codex', side_effect=lambda staging, prompt: shutil.copyfile(p, staging/'continuation.mp4')):
            jobs._worker(*full_spawn.call_args.args)
        self.assertEqual(jobs.list_jobs(self.root)[0]['status'], 'complete', jobs.list_jobs(self.root)[0])
        self.assertEqual(sample_before, (folder/'sample.mp4').read_bytes())
        self.assertGreater(float(jobs._probe(folder/'final.mp4')['format']['duration']), 1.5)
        self.assertTrue(jobs._verify_prefix(folder/'sample.mp4', folder/'final.mp4'))
        rotated = self.root/'rotated.mp4'
        subprocess.run(['ffmpeg','-v','error','-display_rotation:v:0','90','-i',str(folder/'final.mp4'),
                        '-map','0','-c','copy',str(rotated)],check=True)
        self.assertEqual(jobs._probe(rotated,True)['streams'][0]['side_data_list'][0]['rotation'], 90)
        self.assertFalse(jobs._verify_prefix(folder/'sample.mp4', rotated))
        (folder/'sample.mp4').write_bytes(q.read_bytes())
        self.assertIsNone(jobs.media_path(self.root, job['id'], 'sample'))
        with self.assertRaises(jobs.JobError):
            jobs.approve_job(self.root, job['id'])


if __name__ == '__main__':
    unittest.main()
