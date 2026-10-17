import importlib.util,json,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
script=Path(__file__).resolve().parents[1]/'scripts/same.py'
spec=importlib.util.spec_from_file_location('dola_same',script);same=importlib.util.module_from_spec(spec);spec.loader.exec_module(same)
api=same.adapter(Path('.'))

class FakeClient:
    def __init__(self):self.posts=[];self.token='fake-only'
    def clean(self,x):return str(x)
    def call(self,name,**kwargs):
        if name=='models':return 200,{'data':[{'id':'seedance-2.5','durations':[30]}]}
        if name=='submit':self.posts.append(kwargs['blob']);return 202,{'id':'video_fake','status':'queued'}
        raise AssertionError('Unexpected network operation')

class SameRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'source';self.target=self.root/'retry';self.source.mkdir()
        refs=[]
        for i in [1,2]:
            path=self.source/f'r{i}.png';path.write_bytes(b'\x89PNG\r\n\x1a\n'+bytes([i])*128)
            refs.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        self.fields={'model':'seedance-2.5','duration':30,'size':'720x1280','prompt':'林小满{九块九五大块，包邮。}\n奶奶{洗！}'}
        (self.source/'request.json').write_text(json.dumps({**self.fields,'references':refs}),encoding='utf-8')
        self.job={'operation':'submit','source_task_dir':str(self.source),'task_dir':str(self.target),'unit':11,'retry_number':3,'project_root':'.'}
    def test_one_creation_and_verbatim_multipart(self):
        c=FakeClient();report={'provider':'dolasd_video','model':'seedance-2.5','duration':30,'size':'720x1280','concurrency_limit':50,'active_concurrency':0}
        with patch.object(same,'connection',return_value=(c,{'fake':True})),patch.object(api,'live_check',return_value=report):
            first=same.run(self.job);second=same.run(self.job)
        self.assertEqual(len(c.posts),1);self.assertEqual(first['new_submissions'],1);self.assertEqual(second['new_submissions'],0)
        request=json.loads((self.target/'request.json').read_text(encoding='utf-8'))
        self.assertEqual({k:request[k] for k in self.fields},self.fields)
        self.assertIn(self.fields['prompt'].encode(),c.posts[0])
        self.assertLess(c.posts[0].index(bytes([1])*128),c.posts[0].index(bytes([2])*128))
    def test_unknown_outcome_does_not_create_again(self):
        self.target.mkdir();api.append(self.target/'dolasd-tasks.jsonl',{'status':'unknown_outcome'})
        with patch.object(same,'connection',side_effect=AssertionError('Must not reconnect or POST')):
            result=same.run(self.job)
        self.assertEqual(result['new_submissions'],0)
    def test_changed_reference_stops_before_network(self):
        (self.source/'r1.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'changed'*50)
        with patch.object(same,'connection',side_effect=AssertionError('Must not connect')):
            with self.assertRaisesRegex(RuntimeError,'reference files changed'):same.run(self.job,True)
    def test_cumulative_retry_limit(self):
        self.job['retry_number']=4
        with self.assertRaisesRegex(RuntimeError,'retry limit'):same.run(self.job,True)
    def test_poll_without_task_id_never_submits(self):
        self.target.mkdir();api.append(self.target/'dolasd-tasks.jsonl',{'status':'unknown_outcome'})
        self.job.update(operation='poll',output_dir=str(self.root/'outputs'))
        with patch.object(same,'connection',side_effect=AssertionError('Must not connect')):
            with self.assertRaisesRegex(RuntimeError,'No task ID'):same.run(self.job)

if __name__=='__main__':unittest.main()
