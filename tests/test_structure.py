import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class StructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = None
        if importlib.util.find_spec('structure_analysis'):
            import structure_analysis
            cls.module = structure_analysis

    def engine(self):
        self.assertIsNotNone(self.module, '需要本地结构分析模块')
        return self.module

    def fixture(self, folder, audio=False):
        path=Path(folder)/'real-test-fixture.mp4'
        cmd=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x120:rate=10:duration=2']
        if audio: cmd+=['-f','lavfi','-i','sine=frequency=300:duration=2']
        cmd+=['-c:v','libx264','-pix_fmt','yuv420p']
        if audio: cmd+=['-c:a','aac','-shortest']
        subprocess.run(cmd+[str(path)],check=True,capture_output=True)
        return path

    def test_silent_video_keeps_visual_card_without_inventing_speech(self):
        m=self.engine()
        with tempfile.TemporaryDirectory() as d:
            card=m.analyze_file(self.fixture(d),Path(d)/'result')
            self.assertEqual(card['audio']['status'],'no_audio')
            self.assertEqual(card['transcript']['segments'],[])
            self.assertTrue(card['beats'])
            self.assertTrue(card['frames'])
            self.assertTrue(all('observation' in x and 'interpretation' in x for x in card['beats']))
            self.assertNotIn(str(Path(d)),json.dumps(card))

    def test_transcription_failure_does_not_discard_visual_and_audio_evidence(self):
        m=self.engine()
        with tempfile.TemporaryDirectory() as d, patch.object(m,'transcribe_audio',return_value={'status':'failed','segments':[],'message':'本机转写失败','attempts':['auto','zh']}):
            card=m.analyze_file(self.fixture(d,True),Path(d)/'result')
            self.assertEqual(card['transcript']['status'],'failed')
            self.assertEqual(card['audio']['status'],'ok')
            self.assertTrue(card['beats'])

    def test_cancel_terminates_child_process(self):
        m=self.engine()
        cancel=threading.Event()
        timer=threading.Timer(.15,cancel.set);timer.start()
        start=time.monotonic()
        with self.assertRaises(m.AnalysisCancelled):
            m.run_command([sys.executable,'-c','import time;time.sleep(30)'],cancel,60)
        self.assertLess(time.monotonic()-start,3)
        timer.join()

    def test_editor_rejects_invalid_ranges_and_preserves_machine_evidence(self):
        m=self.engine()
        card={'duration':10,'analyzed_duration':10,'beats':[{'start':0,'end':3,'role':'hook','observation':'画面记录','interpretation':'待核对'}],'events':[]}
        with self.assertRaises(ValueError):m.validate_edits(card,{'beats':[{'start':5,'end':2,'role':'hook'}]})
        with self.assertRaises(ValueError):m.validate_edits(card,{'beats':[{'start':0,'end':float('nan'),'role':'hook'}]})
        result=m.validate_edits(card,{'beats':[{'start':0,'end':4,'role':'hook','observation':'经人工核对','interpretation':'可能吸引注意'}]})
        self.assertEqual(result['beats'][0]['end'],4)
        self.assertEqual(card['beats'][0]['end'],3)

    def test_manager_rejects_source_outside_library(self):
        m=self.engine()
        with tempfile.TemporaryDirectory() as d:
            manager=m.AnalysisManager(Path(d))
            with self.assertRaises(ValueError):manager.start({'id':'safe','file':'../../outside.mp4'})


class ReuseTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('structure_workflows'),'需要结构复用模块')
        import structure_workflows
        return structure_workflows

    def test_multi_reference_dimensions_do_not_copy_source_words(self):
        m=self.module()
        cards={'a':{'analyzed_duration':12,'beats':[{'start':0,'end':3,'role':'hook','observation':'不能照抄的原片台词','interpretation':'用问题开场'}]},
               'b':{'analyzed_duration':15,'beats':[{'start':0,'end':4,'role':'hook'},{'start':4,'end':10,'role':'example'},{'start':10,'end':15,'role':'takeaway'}]}}
        structure=m.compose_structure(cards,[{'asset_id':'a','dimensions':['hook']},{'asset_id':'b','dimensions':['pace']}])
        draft=m.draft_script(structure,'在咖啡馆点单',['先说明想喝什么','再确认杯型','最后道谢'])
        self.assertNotIn('不能照抄的原片台词',json.dumps(draft,ensure_ascii=False))
        self.assertIn('在咖啡馆点单',draft['script'])
        self.assertEqual(len(draft['storyboard']),3)
        self.assertEqual(structure['sources'][0]['dimensions'],['hook'])
        self.assertIn('规则草案',draft['method'])

    def test_hook_method_and_specific_visuals_survive_reuse_without_source_copy(self):
        m=self.module()
        card={'analyzed_duration':12,'hook_pattern':'contrast','beats':[{'start':0,'end':3,'role':'hook'}]}
        structure=m.compose_structure({'a':card},[{'asset_id':'a','dimensions':['hook']}])
        self.assertEqual(structure['hook_pattern'],'contrast')
        draft=m.draft_script(structure,'咖啡馆点单',['手指菜单上的拿铁｜Un café con leche, por favor.','手指小杯｜Pequeño, por favor.','接过杯子点头｜Gracias.'])
        self.assertTrue(any('手指小杯' in r['visual'] for r in draft['storyboard']))
        self.assertIn('Gracias.',draft['script'])
        self.assertNotIn('用自己的画面或绘图展示',draft['script'])
        self.assertIn('timing_note',draft)

    def test_empty_dimensions_and_empty_topic_fail(self):
        m=self.module()
        with self.assertRaises(ValueError):m.compose_structure({},[])
        with self.assertRaises(ValueError):m.draft_script({'beats':[]},'',[])


if __name__=='__main__':unittest.main()
