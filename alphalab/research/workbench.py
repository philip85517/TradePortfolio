"""HTTP boundary shared by the dashboard and standalone research workbench."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import unquote, urlparse

from .review import REVIEW_STATIC_ASSETS, ReviewRequestHandler, ReviewState, load_review_run, STATIC_ROOT
from .workflow import ResearchWorkflow, WorkflowError


class WorkbenchHTTPMixin:
    """Call handle_workbench before a host application's own route dispatch."""
    _send_json = ReviewRequestHandler._send_json
    _send_file = ReviewRequestHandler._send_file

    def get_workflow(self):
        return self.workflow

    def _body(self):
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'):
            raise PermissionError('仅允许从当前工作台提交操作')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise PermissionError('仅允许从当前工作台提交操作')
        content_type = self.headers.get('Content-Type', '').split(';')[0]
        if content_type != 'application/json':
            raise WorkflowError('请求必须使用 JSON', 'INVALID_REQUEST')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError as exc:
            raise WorkflowError('请求长度无效') from exc
        if length < 0 or length > 1_000_000:
            raise WorkflowError('请求内容过大')
        try:
            payload = json.loads(self.rfile.read(length) or b'{}',
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite number')))
        except (ValueError, UnicodeDecodeError) as exc:
            raise WorkflowError('请求内容不是有效 JSON') from exc
        if not isinstance(payload, dict):
            raise WorkflowError('请求内容必须是对象')
        return payload

    def handle_workbench(self):
        path = urlparse(self.path).path
        if not (path in {
            '/wizard', '/wizard/', '/wizard.js', '/wizard.css', '/portfolio-review.js', '/portfolio-review.css',
            '/portfolio-review-charts.js', '/vendor/lightweight-charts.standalone.production.js',
        }
                or path.startswith('/api/wizard/') or path.startswith('/research/review/')):
            return False
        try:
            if self.command == 'GET' and path in {
                '/wizard', '/wizard/', '/wizard.js', '/wizard.css', '/portfolio-review.js', '/portfolio-review.css',
                '/portfolio-review-charts.js', '/vendor/lightweight-charts.standalone.production.js',
            }:
                filename = 'wizard.html' if path in {'/wizard', '/wizard/'} else path[1:]
                self._send_file(STATIC_ROOT / filename)
                return True
            if self.command == 'GET' and path.startswith('/research/review/'):
                parts = path.split('/')
                run_id = unquote(parts[3]) if len(parts) > 3 else ''
                if len(parts) == 4:
                    self.send_response(302); self.send_header('Location', path + '/'); self.end_headers()
                    return True
                review_asset = parts[4:]
                asset_name = '/'.join(review_asset)
                if review_asset == ['']:
                    self._send_file(STATIC_ROOT / 'index.html')
                    return True
                if asset_name in REVIEW_STATIC_ASSETS:
                    self._send_file(STATIC_ROOT / REVIEW_STATIC_ASSETS[asset_name])
                    return True
                if any(part in {'.', '..'} for part in review_asset):
                    self._send_json({'error': '静态资源不存在'}, status=404)
                    return True
                if not review_asset or review_asset[0] != 'api':
                    self._send_json({'error': '静态资源不存在'}, status=404)
                    return True
                flow = self.get_workflow()
                run = load_review_run(flow.runs_dir, run_id)
                data_source = run.manifest.get('diagnostics', {}).get('data_source', {})
                data_source = data_source if isinstance(data_source, dict) else {}
                db_path = data_source.get('db_path')
                self.review_state = ReviewState(run, db_path)
                original = self.path
                parsed = urlparse(original)
                self.path = '/' + '/'.join(parts[4:]) + ('?' + parsed.query if parsed.query else '')
                try:
                    ReviewRequestHandler.do_GET(self)
                finally:
                    self.path = original
                return True
            flow = self.get_workflow()
            parts = path.removeprefix('/api/wizard/').strip('/').split('/')
            body = self._body() if self.command != 'GET' else {}
            if parts == ['drafts']:
                if self.command == 'GET':
                    self._send_json({'drafts': flow.list_drafts()})
                elif self.command == 'POST':
                    self._send_json({'draft': flow.create_draft(source_id=body.get('source_id'),
                                                              source_run_id=body.get('source_run_id'),
                                                              source_task_id=body.get('source_task_id'))}, status=201)
                else:
                    self._send_json({'error': '不支持此操作'}, status=405)
            elif len(parts) in {2, 3} and parts[0] == 'drafts':
                draft_id = parts[1]
                revision = body.get('revision')
                if len(parts) == 2 and self.command == 'GET':
                    self._send_json({'draft': flow.get_draft(draft_id)})
                elif len(parts) == 2 and self.command == 'PATCH':
                    draft = flow.update_draft(draft_id, revision, scope=body.get('scope'), portfolio=body.get('portfolio'))
                    self._send_json({'draft': draft})
                elif len(parts) == 3 and self.command == 'POST':
                    action = parts[2]
                    if action == 'check':
                        task = flow.start_check(draft_id, revision)
                        self._send_json({'task': task, 'draft': flow.get_draft(draft_id)}, status=202)
                    elif action == 'retry_source':
                        task = flow.retry_source(draft_id, revision, body.get('symbol'))
                        self._send_json({'task': task, 'draft': flow.get_draft(draft_id)}, status=202)
                    elif action == 'preview':
                        draft = flow.preview(draft_id, revision)
                        self._send_json({'draft': draft, 'preview': draft['preview']})
                    elif action in {'prepare', 'run'}:
                        task = (flow.prepare(draft_id, revision, plan_id=body.get('plan_id')) if action == 'prepare'
                                else flow.run(draft_id, revision, body.get('idempotency_key')))
                        self._send_json({'task': task, 'draft': flow.get_draft(draft_id)}, status=202)
                    else:
                        self._send_json({'error': '操作不存在'}, status=404)
                else:
                    self._send_json({'error': '不支持此操作'}, status=405)
            elif parts == ['runs'] and self.command == 'GET':
                self._send_json({'runs': flow.list_runs()})
            elif len(parts) == 2 and parts[0] == 'tasks' and self.command == 'GET':
                self._send_json({'task': flow.get_task(parts[1])})
            elif len(parts) == 3 and parts[0] == 'tasks' and parts[2] == 'cancel' and self.command == 'POST':
                self._send_json({'task': flow.cancel(parts[1])})
            else:
                self._send_json({'error': '操作不存在'}, status=404)
        except PermissionError as exc:
            self._send_json({'error': str(exc), 'code': 'ORIGIN_DENIED'}, status=403)
        except KeyError as exc:
            self._send_json({'error': str(exc), 'code': 'NOT_FOUND'}, status=404)
        except ValueError as exc:
            code = getattr(exc, 'code', 'INVALID_INPUT')
            self._send_json({'error': str(exc), 'code': code, 'step': getattr(exc, 'step', 1)},
                            status=409 if 'CONFLICT' in code else 400)
        except Exception:
            self._send_json({'error': '工作台暂时无法完成操作；已保存的草稿仍保留，请重试。',
                             'code': 'INTERNAL_ERROR'}, status=500)
        return True


class WorkbenchHandler(WorkbenchHTTPMixin, BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(302); self.send_header('Location', '/wizard'); self.end_headers()
        elif not self.handle_workbench():
            self._send_json({'error': '页面不存在'}, status=404)

    def do_POST(self):
        if not self.handle_workbench():
            self._send_json({'error': '操作不存在'}, status=404)

    do_PATCH = do_POST

    def log_message(self, fmt, *args):
        return


def create_workbench_server(workflow, host='127.0.0.1', port=0):
    class Handler(WorkbenchHandler):
        pass
    Handler.workflow = workflow
    return ThreadingHTTPServer((host, port), Handler)


def serve_workbench(*, directory, db_path='auto', runs_dir=None, host='127.0.0.1', port=8787):
    workflow = ResearchWorkflow(directory, db_path=db_path, runs_dir=runs_dir)
    server = create_workbench_server(workflow, host, port)
    print(f'股票组合创建工作台：http://{host}:{port}/wizard', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
