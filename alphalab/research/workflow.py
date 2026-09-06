"""Durable research drafts and jobs. Browser state is never execution authority."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import json
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from .runs import ResearchRunStore


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class WorkflowError(ValueError):
    def __init__(self, message, code='INVALID_INPUT', step=1):
        super().__init__(message)
        self.code, self.step = code, step


class _Cancelled(Exception):
    pass


class ResearchWorkflow:
    def __init__(self, directory, *, backend=None, db_path='auto', runs_dir=None):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._owner_file = (self.directory / 'server.lock').open('a+')
        try:
            fcntl.flock(self._owner_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._owner_file.close()
            raise WorkflowError('另一个工作台正在使用此目录；请使用已有工作台，或指定独立 workspace-dir', 'WORKSPACE_IN_USE') from exc
        self._closed = False
        self._threads = []
        self.runs_dir = Path(runs_dir).resolve() if runs_dir else self.directory / 'runs'
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / 'workflow.sqlite3'
        self._lock = threading.RLock()
        self._worker_lock = threading.Lock()
        if backend is None:
            from .wizard_backend import WizardResearchBackend
            backend = WizardResearchBackend(db_path=db_path, cache_dir=self.directory / 'cache')
        self.backend = backend
        with self._connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        with self._lock:
            for task in self._all('tasks'):
                if task['status'] in {'QUEUED', 'RUNNING'}:
                    task.update(status='INTERRUPTED', stage='服务已重启，请重新检查数据后继续', updated_at=_now())
                    self._put('tasks', task)

    @contextmanager
    def _connect(self):
        if self._closed:
            raise WorkflowError('此工作台已关闭，请重新打开')
        con = sqlite3.connect(self.db_path, timeout=30)
        try:
            with con:
                yield con
        finally:
            con.close()

    def close(self):
        """Release ownership only after all backend operations have stopped."""
        for thread in self._threads:
            thread.join(timeout=.5)
        if any(thread.is_alive() for thread in self._threads):
            raise WorkflowError('工作台任务仍在执行，请等待完成或取消补数后关闭')
        self._closed = True
        self._owner_file.close()

    def _all(self, table):
        with self._connect() as con:
            return [json.loads(row[0]) for row in con.execute(f'SELECT payload FROM {table}')]

    def _get(self, table, key):
        with self._connect() as con:
            row = con.execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone()
        if not row:
            raise KeyError('未找到草稿或任务，请返回首页')
        return json.loads(row[0])

    def _put(self, table, value):
        with self._connect() as con:
            con.execute(f'INSERT OR REPLACE INTO {table} VALUES (?, ?)',
                        (value['id'], json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def get_draft(self, draft_id):
        with self._lock:
            return self._get('drafts', draft_id)

    def list_drafts(self):
        with self._lock:
            tasks = {task['id']: task for task in self._all('tasks')}
            drafts = self._all('drafts')
            for draft in drafts:
                task = tasks.get(draft.get('task_id'), {})
                draft.update(task_kind=task.get('kind'), task_status=task.get('status'))
            return sorted(drafts, key=lambda d: d['updated_at'], reverse=True)

    def list_runs(self):
        store = ResearchRunStore(self.runs_dir)
        runs = store.list()
        for run in runs:
            manifest = store.manifest(run['run_id'])
            metadata = manifest.get('spec', {}).get('wizard_metadata') or {}
            run.update(name=metadata.get('portfolio', {}).get('name') or run['run_id'],
                       created_at=manifest.get('created_at', ''),
                       can_copy=bool(metadata.get('scope') and metadata.get('portfolio')))
        return list(reversed(runs))

    def create_draft(self, *, source_id=None, source_run_id=None, source_task_id=None):
        with self._lock:
            if sum(bool(x) for x in (source_id, source_run_id, source_task_id)) > 1:
                raise WorkflowError('请选择一个配置来源')
            source = self.get_draft(source_id) if source_id else None
            if source_task_id:
                source = self.get_task(source_task_id).get('configuration')
                if not source:
                    raise WorkflowError('该任务没有保存的配置，请使用原草稿', 'CONFIG_UNAVAILABLE', 1)
            if source_run_id:
                manifest = ResearchRunStore(self.runs_dir).manifest(source_run_id)
                source = manifest.get('spec', {}).get('wizard_metadata')
                if not source or not source.get('scope') or not source.get('portfolio'):
                    raise WorkflowError('该历史运行没有向导配置，请新建组合', 'CONFIG_UNAVAILABLE', 1)
            scope = source['scope'] if source else {
                'market': 'a_share', 'start_date': '', 'end_date': '', 'selection_mode': 'manual',
                'symbols': [], 'rule_version': 'fixed_v0', 'top_n': 10, 'quality_mode': 'strict'}
            portfolio = source['portfolio'] if source else {
                'name': '我的股票组合', 'initial_cash': 100000, 'weighting': 'equal', 'weights': {},
                'commission_rate': .0003, 'slippage_rate': .0005, 'max_single_weight': None,
                'max_industry_weight': None, 'min_holdings': 1}
            draft = {'id': uuid4().hex, 'revision': 0, 'scope': deepcopy(scope),
                     'portfolio': deepcopy(portfolio), 'readiness': None, 'preview': None,
                     'task_id': None, 'created_at': _now(), 'updated_at': _now()}
            self._put('drafts', draft)
            return draft

    def _current(self, draft_id, revision):
        draft = self._get('drafts', draft_id)
        if isinstance(revision, bool) or revision != draft['revision']:
            raise WorkflowError('草稿版本已变化，请重新载入已保存内容', 'REVISION_CONFLICT', 1)
        return draft

    def update_draft(self, draft_id, revision, *, scope=None, portfolio=None):
        with self._lock:
            draft = self._current(draft_id, revision)
            for value in (scope, portfolio):
                if value is not None and not isinstance(value, dict):
                    raise WorkflowError('录入内容必须是对象')
            next_scope = {**draft['scope'], **(scope or {})}
            next_portfolio = {**draft['portfolio'], **(portfolio or {})}
            if set(next_scope) - set(draft['scope']) or set(next_portfolio) - set(draft['portfolio']):
                raise WorkflowError('存在不支持的配置字段')
            if next_scope != draft['scope']:
                draft.update(readiness=None, preview=None, task_id=None)
            elif any(next_portfolio.get(k) != draft['portfolio'].get(k) for k in next_portfolio if k != 'name'):
                draft['preview'] = None
            if next_portfolio != draft['portfolio'] and draft.get('task_id'):
                if self.get_task(draft['task_id'])['kind'] == 'run':
                    draft['task_id'] = None
            draft.update(scope=next_scope, portfolio=next_portfolio, revision=draft['revision'] + 1, updated_at=_now())
            self._put('drafts', draft)
            return draft

    def check(self, draft_id, revision):
        with self._lock:
            draft = self._current(draft_id, revision)
        result = self.backend.inspect(deepcopy(draft['scope']))
        with self._lock:
            current = self._current(draft_id, revision)
            current.update(readiness=self._readiness(result), preview=None, updated_at=_now())
            self._put('drafts', current)
            return current

    @staticmethod
    def _readiness(result):
        return {**result, 'readiness_id': uuid4().hex, 'checked_at': _now()}

    def _ready(self, draft):
        if not draft.get('readiness') or draft['readiness'].get('status') != 'READY':
            raise WorkflowError('数据尚未准备就绪，请先检查并补齐历史数据', 'DATA_NOT_READY', 2)

    def preview(self, draft_id, revision):
        with self._lock:
            draft = self._current(draft_id, revision)
            self._ready(draft)
        try:
            result = self.backend.preview(deepcopy(draft['scope']), deepcopy(draft['portfolio']), deepcopy(draft['readiness']))
        except ValueError as exc:
            code = str(exc).split('：', 1)[0]
            if code in {'DATA_CHANGED', 'DATA_NOT_READY'}:
                with self._lock:
                    current = self._current(draft_id, revision)
                    if (current.get('readiness') or {}).get('readiness_id') == draft['readiness']['readiness_id']:
                        current.update(readiness=None, preview=None, updated_at=_now())
                        self._put('drafts', current)
                raise WorkflowError(str(exc), code, 2) from exc
            raise WorkflowError(str(exc), 'PORTFOLIO_INVALID', 3) from exc
        with self._lock:
            current = self._current(draft_id, revision)
            if (current.get('readiness') or {}).get('readiness_id') != draft['readiness']['readiness_id']:
                raise WorkflowError('数据检查已变化，请重新预览', 'DATA_CHANGED', 2)
            current.update(preview=result, updated_at=_now())
            self._put('drafts', current)
            return current

    def get_task(self, task_id):
        with self._lock:
            return self._get('tasks', task_id)

    def _new_task(self, draft, kind, key):
        task = {'id': uuid4().hex, 'draft_id': draft['id'], 'revision': draft['revision'],
                'scope_key': _digest(draft['scope']), 'kind': kind, 'idempotency_key': key,
                'status': 'QUEUED', 'stage': '等待执行', 'error': None, 'result': None,
                'configuration': {key: deepcopy(draft[key]) for key in ('scope', 'portfolio', 'readiness')},
                'created_at': _now(), 'updated_at': _now()}
        self._put('tasks', task)
        draft.update(task_id=task['id'], updated_at=_now())
        self._put('drafts', draft)
        thread = threading.Thread(target=self._execute, args=(task['id'], deepcopy(draft)), daemon=True)
        self._threads.append(thread)
        thread.start()
        return task

    def prepare(self, draft_id, revision):
        with self._lock:
            draft = self._current(draft_id, revision)
            for task in self._all('tasks'):
                if (task['draft_id'] == draft_id and task['kind'] == 'prepare'
                    and task['revision'] == revision and task['status'] in {'QUEUED', 'RUNNING'}):
                    return task
            draft.update(readiness=None, preview=None)
            return self._new_task(draft, 'prepare', uuid4().hex)

    def run(self, draft_id, revision, idempotency_key):
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 200:
            raise WorkflowError('缺少有效提交标识，请刷新后重试', 'INVALID_SUBMISSION', 4)
        with self._lock:
            draft = self._current(draft_id, revision)
            for task in self._all('tasks'):
                if task['draft_id'] == draft_id and task['kind'] == 'run':
                    if task['idempotency_key'] == idempotency_key:
                        if task['revision'] != revision:
                            raise WorkflowError('配置已改变，请使用新的提交', 'SUBMISSION_CONFLICT', 4)
                        return task
                    if task['revision'] == revision and task['status'] in {'QUEUED', 'RUNNING', 'SUCCEEDED'}:
                        return task
            self._ready(draft)
            if not draft.get('preview'):
                raise WorkflowError('请先完成组合预览', 'PREVIEW_REQUIRED', 3)
        fresh = self.backend.inspect(deepcopy(draft['scope']))
        with self._lock:
            current = self._current(draft_id, revision)
            self._ready(current)
            if not current.get('preview'):
                raise WorkflowError('组合预览已变化，请重新预览', 'PREVIEW_REQUIRED', 3)
            if (fresh.get('status') != 'READY'
                or fresh.get('data_identity') != draft['readiness'].get('data_identity')):
                current.update(readiness=None, preview=None, updated_at=_now())
                self._put('drafts', current)
                raise WorkflowError('数据已变化，请重新检查；已填写的配置仍然保留', 'DATA_CHANGED', 2)
            # Another request may have submitted while this request inspected data.
            for task in self._all('tasks'):
                if (task['draft_id'] == draft_id and task['kind'] == 'run'
                    and task['idempotency_key'] == idempotency_key):
                    if task['revision'] != revision:
                        raise WorkflowError('配置已改变，请使用新的提交', 'SUBMISSION_CONFLICT', 4)
                    return task
                if (task['draft_id'] == draft_id and task['kind'] == 'run' and task['revision'] == revision
                    and task['status'] in {'QUEUED', 'RUNNING', 'SUCCEEDED'}):
                    return task
            return self._new_task(current, 'run', idempotency_key)

    def cancel(self, task_id):
        with self._lock:
            task = self._get('tasks', task_id)
            if task['kind'] != 'prepare':
                raise WorkflowError('模拟已冻结提交，请等待结果；仅数据准备可以取消', 'NOT_CANCELLABLE', 4)
            if task['status'] in {'QUEUED', 'RUNNING'}:
                task.update(status='CANCELLED', stage='已取消，已完成的有效缓存保留', updated_at=_now())
                self._put('tasks', task)
            return task

    def _execute(self, task_id, draft):
        def cancelled():
            return self.get_task(task_id)['status'] in {'CANCELLED', 'INTERRUPTED'}

        def progress(message):
            with self._lock:
                if cancelled():
                    raise _Cancelled()
                task = self._get('tasks', task_id)
                task.update(status='RUNNING', stage=str(message), updated_at=_now())
                self._put('tasks', task)

        # One backend operation at a time keeps mutable provider caches consistent.
        with self._worker_lock:
            try:
                progress('检查研究数据' if self.get_task(task_id)['kind'] == 'prepare' else '运行历史模拟')
                task = self.get_task(task_id)
                if task['kind'] == 'prepare':
                    result = self.backend.prepare(deepcopy(draft['scope']), progress, cancelled)
                    status = 'SUCCEEDED' if result.get('status') == 'READY' else 'PARTIAL'
                else:
                    result = self.backend.run(deepcopy(draft['scope']), deepcopy(draft['portfolio']),
                                              deepcopy(draft['readiness']), self.runs_dir)
                    status = 'SUCCEEDED'
                with self._lock:
                    if cancelled():
                        return
                    task = self._get('tasks', task_id)
                    task.update(status=status, result=result, stage='已完成' if status == 'SUCCEEDED' else '仍有数据缺口，请处理后继续', updated_at=_now())
                    self._put('tasks', task)
                    current = self._get('drafts', draft['id'])
                    if _digest(current['scope']) == task['scope_key'] and current.get('task_id') == task_id:
                        if task['kind'] == 'prepare':
                            current.update(readiness=self._readiness(result), preview=None)
                        current['updated_at'] = _now()
                        self._put('drafts', current)
            except _Cancelled:
                return
            except Exception as exc:
                with self._lock:
                    if cancelled():
                        return
                    task = self._get('tasks', task_id)
                    task.update(status='FAILED', error=str(exc), stage='执行失败；配置和已完成缓存保留，可重试', updated_at=_now())
                    self._put('tasks', task)
