import json
import time
from pathlib import Path
import unittest
import test_server
import test_structure


class StructureAPITests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request
    token=test_server.ServerTests.token
    def test_local_analysis_edit_reuse_survives_manager_restart(self):
        from structure_analysis import AnalysisManager
        fixture=test_structure.StructureTests().fixture(self.root)
        folder=self.root/'素材';folder.mkdir()
        target=folder/'input.mp4';target.write_bytes(fixture.read_bytes())
        (self.root/'catalog.json').write_text(json.dumps([{'id':'clip','file':'素材/input.mp4','kind':'video'}]))
        token=self.token()
        route='/api/assets/clip/analysis'
        self.assertEqual(self.request('POST',route,{})[0],403)
        self.assertEqual(self.request('POST',route,{},token)[0],202)
        for _ in range(100):
            response=self.request('GET',route)
            self.assertEqual(response[0],200)
            state=json.loads(response[2])
            if state['status'] not in ('running','cancelling'):break
            time.sleep(.1)
        self.assertEqual(state['status'],'ready',state)
        card=state['card'];card['beats'][0]['observation']='人工回看后的修改'
        self.assertEqual(self.request('POST','/api/assets/clip/structure',{'beats':card['beats']},token)[0],200)
        restored=AnalysisManager(self.root).get('clip')
        self.assertEqual(restored['card']['beats'][0]['observation'],'人工回看后的修改')
        body={'title':'我的可复用流程','selections':[{'asset_id':'clip','dimensions':['hook','pace']}],'base_skill':''}
        status,_,raw=self.request('POST','/api/structure-workflows',body,token)
        self.assertEqual(status,201,raw)
        workflow=json.loads(raw)
        status,_,raw=self.request('POST','/api/structure-drafts',{'workflow_id':workflow['id'],'topic':'新的主题','points':['自己的要点']},token)
        self.assertEqual(status,200,raw)
        self.assertIn('新的主题',json.loads(raw)['script'])
        self.assertNotIn('人工回看后的修改',raw.decode())
        frame=card['frames'][0]['file']
        self.assertEqual(self.request('GET',f'/media/analysis/clip/{frame}')[0],200)
        self.assertEqual(self.request('GET','/media/analysis/clip/../../catalog.json')[0],404)

    def test_separate_data_directory_still_serves_code_ui(self):
        self.server.ui_root=Path(__file__).resolve().parents[1]/'ui'
        status,_,raw=self.request('GET','/')
        self.assertEqual(status,200)
        self.assertIn('视频工作台',raw.decode())
