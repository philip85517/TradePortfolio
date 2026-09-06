"""User-visible draft/task workflow; providers replaced, persistence remains real."""
import time
import threading
from pathlib import Path

import pytest


class SampleBackend:
    def __init__(self):
        self.available = False
        self.identity = 'sample-v1'
        self.release = None

    def inspect(self, scope):
        return {'status': 'READY' if self.available else 'BLOCKED',
                'issues': [] if self.available else [{'code': 'MISSING_DATA', 'message': '缺少历史行情', 'action': '补齐数据'}],
                'data_identity': self.identity, 'dates': {'entry_date': scope['start_date'], 'end_date': scope['end_date']},
                'coverage': {'ready': int(self.available), 'total': 1}}

    def prepare(self, scope, progress, cancelled):
        progress('正在补齐行情')
        if self.release:
            self.release.wait(3)
        self.available = True
        return self.inspect(scope)

    def preview(self, scope, portfolio, readiness):
        if portfolio['initial_cash'] < 1000:
            raise ValueError('本金不足，无法买入一手')
        return {'holdings': [{'symbol': '600000', 'quantity': 100}], 'cash': 1000}

    def run(self, scope, portfolio, readiness, runs_dir):
        return {'run_id': 'sample-run', 'name': portfolio['name']}


def service(tmp_path, backend=None):
    from alphalab.research.workflow import ResearchWorkflow
    return ResearchWorkflow(tmp_path, backend=backend or SampleBackend())


def scope():
    return {'market': 'a_share', 'start_date': '2021-03-01', 'end_date': '2025-06-30',
            'selection_mode': 'manual', 'symbols': ['600000'], 'quality_mode': 'exploratory', 'rule_version': 'fixed_v0', 'top_n': 10}


def configured(flow):
    draft = flow.create_draft()
    return flow.update_draft(draft['id'], draft['revision'], scope=scope(), portfolio={'name': '历史组合', 'initial_cash': 100000})


def finish(flow, task_id):
    for _ in range(200):
        task = flow.get_task(task_id)
        if task['status'] not in {'QUEUED', 'RUNNING'}:
            return task
        time.sleep(.01)
    raise AssertionError('task did not finish')


def test_new_workflow_is_available():
    import importlib.util
    assert importlib.util.find_spec('alphalab.research.workflow'), '缺少可恢复的创建工作流'


def test_data_gate_prepare_preview_run_reopen(tmp_path):
    flow = service(tmp_path)
    draft = configured(flow)
    with pytest.raises(ValueError, match='数据'):
        flow.preview(draft['id'], draft['revision'])
    checked = flow.check(draft['id'], draft['revision'])
    assert checked['readiness']['status'] == 'BLOCKED'
    task = flow.prepare(draft['id'], draft['revision'])
    assert finish(flow, task['id'])['status'] == 'SUCCEEDED'
    ready = flow.get_draft(draft['id'])
    assert ready['readiness']['status'] == 'READY'
    preview = flow.preview(draft['id'], draft['revision'])
    assert preview['preview']['holdings'][0]['symbol'] == '600000'
    run = flow.run(draft['id'], draft['revision'], 'submit-once')
    assert finish(flow, run['id'])['result']['run_id'] == 'sample-run'
    assert flow.run(draft['id'], draft['revision'], 'submit-once')['id'] == run['id']
    flow.close()
    reopened = service(tmp_path)
    assert reopened.get_draft(draft['id'])['portfolio']['name'] == '历史组合'
    assert reopened.get_task(run['id'])['result']['run_id'] == 'sample-run'


def test_modified_scope_invalidates_only_dependencies_and_rejects_old_revision(tmp_path):
    backend = SampleBackend(); backend.available = True
    flow = service(tmp_path, backend)
    draft = configured(flow)
    flow.check(draft['id'], draft['revision'])
    flow.preview(draft['id'], draft['revision'])
    renamed = flow.update_draft(draft['id'], draft['revision'], portfolio={'name': '改名'})
    assert renamed['readiness']['status'] == 'READY'
    assert renamed['preview'] is not None
    with pytest.raises(ValueError, match='版本'):
        flow.update_draft(draft['id'], draft['revision'], portfolio={'name': '旧请求'})
    changed = flow.update_draft(draft['id'], renamed['revision'], scope={**scope(), 'end_date': '2024-06-28'})
    assert changed['readiness'] is None and changed['preview'] is None
    assert changed['portfolio']['name'] == '改名'


def test_late_preparation_cannot_unlock_changed_scope(tmp_path):
    backend = SampleBackend(); backend.release = threading.Event()
    flow = service(tmp_path, backend)
    draft = configured(flow)
    task = flow.prepare(draft['id'], draft['revision'])
    changed = flow.update_draft(draft['id'], draft['revision'], scope={**scope(), 'end_date': '2023-06-30'})
    backend.release.set()
    finish(flow, task['id'])
    assert flow.get_draft(changed['id'])['readiness'] is None


def test_changed_data_blocks_submission_and_retains_inputs(tmp_path):
    backend = SampleBackend(); backend.available = True
    flow = service(tmp_path, backend)
    draft = configured(flow)
    flow.check(draft['id'], draft['revision'])
    flow.preview(draft['id'], draft['revision'])
    backend.identity = 'sample-v2'
    with pytest.raises(ValueError, match='数据'):
        flow.run(draft['id'], draft['revision'], 'changed-data')
    assert flow.get_draft(draft['id'])['portfolio']['name'] == '历史组合'


def test_cancel_preserves_draft_and_does_not_unlock(tmp_path):
    backend = SampleBackend(); backend.release = threading.Event()
    flow = service(tmp_path, backend)
    draft = configured(flow)
    task = flow.prepare(draft['id'], draft['revision'])
    flow.cancel(task['id']); backend.release.set()
    assert finish(flow, task['id'])['status'] == 'CANCELLED'
    assert flow.get_draft(draft['id'])['readiness'] is None


def test_http_gate_and_revision_contract(tmp_path):
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from alphalab.research.workbench import create_workbench_server
    flow = service(tmp_path)
    server = create_workbench_server(flow)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = 'http://127.0.0.1:' + str(server.server_address[1])
    def request(path, payload=None, method=None):
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'}, method=method)
        try:
            with urlopen(req) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
    try:
        status, data = request('/api/wizard/drafts', {}, 'POST')
        assert status == 201
        draft = data['draft']; path = '/api/wizard/drafts/' + draft['id']
        status, data = request(path, {'revision': 0, 'scope': scope()}, 'PATCH')
        assert status == 200 and data['draft']['revision'] == 1
        status, error = request(path + '/preview', {'revision': 1}, 'POST')
        assert status == 400 and error['step'] == 2
        status, error = request(path, {'revision': 0, 'portfolio': {'name': 'stale'}}, 'PATCH')
        assert status == 409 and error['code'] == 'REVISION_CONFLICT'
        req = Request(base + '/api/wizard/drafts', data=b'{}', method='POST',
                      headers={'Origin': 'https://unrelated.example', 'Content-Type': 'application/json'})
        with pytest.raises(HTTPError) as denied:
            urlopen(req)
        assert denied.value.code == 403
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_workbench_cli_is_discoverable():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'alphalab', 'research', 'workbench', '--help'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '--workspace-dir' in result.stdout


def test_partial_preparation_exposes_remaining_issues(tmp_path):
    class MissingBackend(SampleBackend):
        def prepare(self, scope, progress, cancelled):
            progress('部分行情已保存')
            return self.inspect(scope)
    flow = service(tmp_path, MissingBackend()); draft = configured(flow)
    task = flow.prepare(draft['id'], draft['revision'])
    result = finish(flow, task['id'])
    assert result['status'] == 'PARTIAL'
    assert result['result']['issues'][0]['code'] == 'MISSING_DATA'
    with pytest.raises(ValueError, match='数据'):
        flow.preview(draft['id'], draft['revision'])


def test_rename_during_preparation_retains_valid_data_result(tmp_path):
    backend = SampleBackend(); backend.release = threading.Event()
    flow = service(tmp_path, backend); draft = configured(flow)
    task = flow.prepare(draft['id'], draft['revision'])
    flow.update_draft(draft['id'], draft['revision'], portfolio={'name': '保留新名称'})
    backend.release.set(); finish(flow, task['id'])
    reopened = flow.get_draft(draft['id'])
    assert reopened['portfolio']['name'] == '保留新名称'
    assert reopened['readiness']['status'] == 'READY'


def test_restart_marks_interrupted_jobs_without_losing_draft(tmp_path):
    import json
    import subprocess
    import sys
    script = """
import json, sys, threading, time
from pathlib import Path
from alphalab.tests.integration.test_workflow import service, configured, SampleBackend
backend=SampleBackend(); backend.release=threading.Event()
flow=service(Path(sys.argv[1]),backend); draft=configured(flow)
task=flow.prepare(draft['id'],draft['revision'])
print(json.dumps({'draft_id':draft['id'],'task_id':task['id']}),flush=True)
time.sleep(30)
"""
    process=subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdout=subprocess.PIPE,text=True)
    try:
        saved=json.loads(process.stdout.readline())
    finally:
        process.terminate(); process.wait(timeout=5)
    restored=service(tmp_path)
    assert restored.get_task(saved['task_id'])['status']=='INTERRUPTED'
    assert restored.get_draft(saved['draft_id'])['scope']==scope()


def test_copy_frozen_run_creates_new_draft_without_changing_history(tmp_path):
    import json
    flow = service(tmp_path)
    run_dir = flow.runs_dir / 'historical-run'; run_dir.mkdir()
    manifest = {'run_id': 'historical-run', 'spec': {'wizard_metadata': {
        'scope': scope(), 'portfolio': {'name': '旧组合', 'initial_cash': 200000}}}}
    encoded = json.dumps(manifest)
    (run_dir / 'manifest.json').write_text(encoded)
    draft = flow.create_draft(source_run_id='historical-run')
    assert draft['scope'] == scope()
    assert draft['portfolio']['initial_cash'] == 200000
    assert draft['readiness'] is None
    assert (run_dir / 'manifest.json').read_text() == encoded


def test_failed_run_retains_frozen_configuration_after_draft_edit(tmp_path):
    class FailingBackend(SampleBackend):
        def run(self, *args):
            self.release.wait(3)
            raise ValueError('提供方失败')
    backend=FailingBackend(); backend.available=True; backend.release=threading.Event()
    flow=service(tmp_path,backend); draft=configured(flow)
    flow.check(draft['id'],draft['revision']); flow.preview(draft['id'],draft['revision'])
    task=flow.run(draft['id'],draft['revision'],'frozen-failure')
    flow.update_draft(draft['id'],draft['revision'],portfolio={'initial_cash':200000})
    backend.release.set(); failed=finish(flow,task['id'])
    assert failed['configuration']['portfolio']['initial_cash']==100000
    restored=flow.create_draft(source_task_id=task['id'])
    assert restored['portfolio']['initial_cash']==100000


def test_preparation_started_during_preview_returns_data_changed(tmp_path):
    class SlowPreview(SampleBackend):
        def preview(self,*args):
            entered.set(); released.wait(3)
            return super().preview(*args)
    entered=threading.Event(); released=threading.Event()
    backend=SlowPreview(); backend.available=True; backend.release=threading.Event()
    flow=service(tmp_path,backend); draft=configured(flow); flow.check(draft['id'],draft['revision'])
    errors=[]
    def preview():
        try: flow.preview(draft['id'],draft['revision'])
        except Exception as exc: errors.append(exc)
    worker=threading.Thread(target=preview); worker.start(); assert entered.wait(2)
    task=flow.prepare(draft['id'],draft['revision']); released.set(); worker.join(2)
    assert len(errors)==1 and getattr(errors[0],'code',None)=='DATA_CHANGED'
    backend.release.set(); finish(flow,task['id'])


def test_same_submission_remains_idempotent_after_overlapping_failure(tmp_path):
    class SlowInspect(SampleBackend):
        def inspect(self,scope):
            if threading.current_thread().name=='slow-submit':
                entered.set(); released.wait(3)
            return super().inspect(scope)
        def run(self,*args):
            raise ValueError('模拟失败')
    entered=threading.Event(); released=threading.Event()
    backend=SlowInspect(); backend.available=True
    flow=service(tmp_path,backend); draft=configured(flow)
    flow.check(draft['id'],draft['revision']); flow.preview(draft['id'],draft['revision'])
    results=[]
    worker=threading.Thread(name='slow-submit',target=lambda:results.append(flow.run(draft['id'],draft['revision'],'same-key')))
    worker.start(); assert entered.wait(2)
    first=flow.run(draft['id'],draft['revision'],'same-key'); finish(flow,first['id'])
    released.set(); worker.join(3)
    assert results[0]['id']==first['id']


def test_second_workbench_cannot_steal_active_workspace(tmp_path):
    backend=SampleBackend(); backend.release=threading.Event()
    flow=service(tmp_path,backend); draft=configured(flow)
    task=flow.prepare(draft['id'],draft['revision'])
    with pytest.raises(ValueError,match='工作台'):
        service(tmp_path)
    assert flow.get_task(task['id'])['status'] in {'QUEUED','RUNNING'}
    backend.release.set(); finish(flow,task['id'])


def test_http_real_research_run_and_frozen_review(tmp_path):
    import json
    from urllib.request import Request, urlopen
    from alphalab.tests.integration.test_wizard_backend import make_backend
    from alphalab.research.workbench import create_workbench_server
    backend,_=make_backend(tmp_path/'cache')
    flow=service(tmp_path,backend)
    server=create_workbench_server(flow)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    def request(path,body=None,method=None):
        req=Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json'},method=method)
        with urlopen(req) as response:
            return json.load(response)
    try:
        draft=request('/api/wizard/drafts',{},'POST')['draft']; path='/api/wizard/drafts/'+draft['id']
        request_scope={**scope(),'symbols':['000001','600000'],'start_date':'2025-06-02','end_date':'2025-06-06'}
        draft=request(path,{'revision':draft['revision'],'scope':request_scope},'PATCH')['draft']
        checked=request(path+'/check',{'revision':draft['revision']},'POST')['draft']
        assert checked['readiness']['status']=='READY'
        preview=request(path+'/preview',{'revision':draft['revision']},'POST')['preview']
        assert len(preview['holdings'])==2
        task=request(path+'/run',{'revision':draft['revision'],'idempotency_key':'real-http'},'POST')['task']
        for _ in range(300):
            task=request('/api/wizard/tasks/'+task['id'])['task']
            if task['status'] not in {'RUNNING','QUEUED'}: break
            time.sleep(.02)
        assert task['status']=='SUCCEEDED',task
        run_id=task['result']['run_id']
        assert any(r['run_id']==run_id for r in request('/api/wizard/runs')['runs'])
        prefix='/research/review/'+run_id+'/'
        summary=request(prefix+'api/summary')
        selection=request(prefix+'api/stock?symbol=000001&mode=selection')
        evaluation=request(prefix+'api/stock?symbol=000001&mode=evaluation')
        assert summary['run_id']==run_id
        assert max(row['date'] for row in selection['rows'])<=summary['signal_date']
        assert max(row['date'] for row in evaluation['rows'])>summary['signal_date']
        copied=request('/api/wizard/drafts',{'source_run_id':run_id},'POST')['draft']
        assert copied['scope']==request_scope and copied['readiness'] is None
    finally:
        server.shutdown(); server.server_close(); thread.join(2); flow.close()


def test_recent_runs_use_saved_name_and_drafts_show_completion(tmp_path):
    import json
    backend=SampleBackend(); backend.available=True
    flow=service(tmp_path,backend); draft=configured(flow)
    flow.check(draft['id'],draft['revision']); flow.preview(draft['id'],draft['revision'])
    task=flow.run(draft['id'],draft['revision'],'listed'); finish(flow,task['id'])
    item=flow.list_drafts()[0]
    assert item['task_kind']=='run' and item['task_status']=='SUCCEEDED'
    run_dir=flow.runs_dir/'sample-run'; run_dir.mkdir()
    manifest={'run_id':'sample-run','spec':{'wizard_metadata':{'scope':scope(),'portfolio':draft['portfolio']}}}
    (run_dir/'manifest.json').write_text(json.dumps(manifest))
    item=flow.list_runs()[0]
    assert item['name']=='历史组合' and item['can_copy'] is True


def test_stale_preview_returns_to_data_check(tmp_path):
    class ChangedBackend(SampleBackend):
        def preview(self, *args):
            raise ValueError('DATA_CHANGED：数据或范围已变化，请重新检查数据')
    backend=ChangedBackend(); backend.available=True
    flow=service(tmp_path,backend); draft=configured(flow)
    flow.check(draft['id'],draft['revision'])
    with pytest.raises(ValueError) as error:
        flow.preview(draft['id'],draft['revision'])
    assert error.value.code=='DATA_CHANGED' and error.value.step==2
    saved=flow.get_draft(draft['id'])
    assert saved['readiness'] is None and saved['preview'] is None


def test_editing_completed_portfolio_restores_unfinished_draft(tmp_path):
    backend=SampleBackend(); backend.available=True
    flow=service(tmp_path,backend); draft=configured(flow)
    flow.check(draft['id'],draft['revision']); flow.preview(draft['id'],draft['revision'])
    task=flow.run(draft['id'],draft['revision'],'edit-complete'); finish(flow,task['id'])
    changed=flow.update_draft(draft['id'],draft['revision'],portfolio={'initial_cash':123456})
    assert changed['task_id'] is None and changed['preview'] is None
    assert flow.get_task(task['id'])['configuration']['portfolio']['initial_cash']==100000
