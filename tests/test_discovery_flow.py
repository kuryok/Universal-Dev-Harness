from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from udh.discovery_flow import (
    DiscoveryConfig,
    DiscoveryFlowError,
    _resolve_zen_secret,
    _validate_summary_out_path,
    run_opencode_zen_trial,
)
from tests.test_runtime_and_gateway import _FakeHTTPResponse, _FakeHTTPSConnection
from udh.domain import CoreError
from udh.gateway import GatewayConfig, InferenceGateway
from udh.service import CoreService
from udh.spec_handoff import SpecValidationResult
from udh.store import SQLiteStore
from udh.transitions import ConflictError
from udh.trust_store import TrustStore


SKILL = (Path(__file__).resolve().parent.parent / 'openspec/changes/discover-plan-tickets'
         / 'skills/grill-with-docs-udh/SKILL.md')
GAP = {'id': 'restart', 'impact': 'behavior', 'reason': 'Muda a tela exibida.', 'question': 'Restaurar a última tela?'}
DRAFT = {'proposal': '## Why\nPreservar a última tela.\n', 'tasks': '## 1. Tela\n- [ ] 1.1 Restaurar tela\n',
         'specs': {'view': '## ADDED Requirements\n\n### Requirement: Restore the last view\n'
                           'The app SHALL restore the last view.\n\n#### Scenario: Restart\n'
                           '- **WHEN** the app restarts\n- **THEN** the last view is restored\n'}}


class _UDSConnection(HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


class DiscoveryFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.core = CoreService(SQLiteStore(self.root / 'app.db'))
        self.core.initialize(schema_version=4)
        for version in range(5, 9):
            self.core.migrate(version)
        self.trust = TrustStore(self.root / 'protected')

    def flow(self, port=11434, **changes):
        options = dict(skill_path=SKILL, port=port, model='test-model', trust_store=self.trust,
                       cli_path=Path(os.environ.get('UDH_TEST_OPENSPEC', '/missing/openspec')),
                       node_dir=Path(os.environ.get('UDH_TEST_OPENSPEC', '/missing/openspec')).parent)
        return self.core.discovery_flow(DiscoveryConfig(**(options | changes)))

    @contextmanager
    def server(self, replies):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, body, self.headers.get('Authorization')))
                message = {'role': 'assistant', 'content': json.dumps(replies.pop(0))}
                raw = json.dumps({'choices': [{'message': message, 'finish_reason': 'stop'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server.server_port, calls
            finally:
                server.shutdown()
                thread.join()

    def pi_boundary(self, *, workspace, gateway, prompt, system_prompt, model, allowed_tools, **kwargs):
        self.assertNotEqual(workspace, self.root)
        self.assertFalse(list(workspace.iterdir()))
        self.assertEqual(allowed_tools, ())
        self.assertIn(self.trust.root, kwargs['forbidden_rw_roots'])
        client = _UDSConnection(gateway.socket_path)
        try:
            body = {'model': model, 'stream': True, 'messages': [
                {'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': prompt}]}
            client.request('POST', '/v1/chat/completions', json.dumps(body).encode(),
                           {'Authorization': 'Bearer ' + gateway.capability_token, 'Content-Type': 'application/json',
                            'X-UDH-Purpose': 'inference'})
            response = client.getresponse()
            raw = response.read().decode()
        finally:
            client.close()
        text = ''.join(json.loads(line[6:])['choices'][0]['delta'].get('content', '')
                       for line in raw.splitlines() if line.startswith('data: {'))
        return SimpleNamespace(ok=response.status == 200 and '[DONE]' in raw, assistant_text=text)

    def test_migration_and_skill_preflight(self):
        with self.assertRaises(CoreError):
            self.flow().create('sample', 'Uma ideia.')
        self.assertTrue(self.core.migrate(9)['changed'])
        self.assertFalse(self.core.migrate(9)['changed'])
        self.assertEqual(self.core.store.deep_check()['schema_version'], 9)
        wrong = self.root / 'wrong.md'
        wrong.write_text('alterado', encoding='utf-8')
        for path in (wrong, self.root / 'absent.md'):
            with self.subTest(path=path.name), self.assertRaises(DiscoveryFlowError):
                self.flow(skill_path=path).create('sample', 'Uma ideia.')

    def test_failed_and_stale_invocations_consume_budget(self):
        self.core.migrate(9)
        flow = self.flow()
        session = flow.create('sample', 'Uma ideia.')
        sid = session['session_id']
        with patch('udh.discovery_flow.PiAdapter.run', return_value=SimpleNamespace(ok=True, assistant_text='impróprio')):
            for revision in range(3):
                with self.assertRaises(DiscoveryFlowError):
                    flow.discover(sid, revision)
        current = flow.show(sid)
        self.assertEqual((current['revision'], current['discovery_attempts'], current['rounds']), (3, 3, 0))
        with self.assertRaises(ConflictError):
            flow.discover(sid, 3)

        session = flow.create('sample', 'Uma ideia.')
        sid = session['session_id']

        def race(**kwargs):
            with patch('udh.discovery_flow.PiAdapter.run', return_value=SimpleNamespace(ok=True, assistant_text='{"gaps":[]}')):
                flow.discover(sid, 1)
            return SimpleNamespace(ok=True, assistant_text=json.dumps({'gaps': [GAP]}))

        with patch('udh.discovery_flow.PiAdapter.run', side_effect=race), self.assertRaises(ConflictError):
            flow.discover(sid, 0)
        self.assertEqual(flow.show(sid)['open_gaps'], [])

    def test_gateway_denies_unlisted_model_and_tools_before_upstream(self):
        with self.server([]) as (port, calls), InferenceGateway(GatewayConfig(
                run_id='test-local-discovery', policy_sha256='a' * 64,
                audit_path=self.root / 'audit.jsonl', backend='local_discovery',
                allowed_models=('test-model',), local_discovery_port=port, max_calls=1)) as gateway:
            body = {'model': 'test-model', 'stream': True, 'messages': [{'role': 'user', 'content': 'Ideia.'}]}
            for payload in (body | {'model': 'another-model'},
                            body | {'tools': [{'type': 'function', 'function': {'name': 'read'}}]}):
                client = _UDSConnection(gateway.socket_path)
                try:
                    client.request('POST', '/v1/chat/completions', json.dumps(payload).encode(),
                                   {'Authorization': 'Bearer ' + gateway.capability_token,
                                    'X-UDH-Purpose': 'inference'})
                    response = client.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()
                finally:
                    client.close()
            self.assertEqual(calls, [])

    def test_cli_failure_and_concurrent_answer_change_do_not_advance(self):
        self.core.migrate(9)
        flow = self.flow(cli_path=Path('/missing/openspec'))
        session = flow.create('sample', 'Uma ideia.')
        sid = session['session_id']
        with patch('udh.discovery_flow.PiAdapter.run', return_value=SimpleNamespace(
                ok=True, assistant_text=json.dumps({'gaps': [GAP]}))):
            session = flow.discover(sid, 0)
        answer = 'Restaurar a última tela.'
        session = flow.answer(sid, session['revision'], 'restart', answer)
        session = flow.confirm(sid, session['revision'], 'restart', hashlib.sha256(answer.encode()).hexdigest())
        with patch('udh.discovery_flow.PiAdapter.run', return_value=SimpleNamespace(
                ok=True, assistant_text=json.dumps(DRAFT))):
            session = flow.draft(sid, session['revision'])
        session = flow.approve(sid, session['revision'], session['snapshot_sha256'], 'human-approval')
        with self.assertRaisesRegex(DiscoveryFlowError, 'CLI_MISSING'):
            flow.handoff(sid, session['revision'], 'ready')
        self.assertEqual(flow.show(sid)['status'], 'DRAFT')

        def concurrent(change_id, revision, artifacts, *, approval, **kwargs):
            flow.answer(sid, session['revision'], 'restart', 'Abrir sempre na tela inicial.')
            return SpecValidationResult(change_id, revision, approval[2], 'VALIDATED', '2.1.1')

        with patch('udh.discovery_flow.validate_spec_snapshot', side_effect=concurrent), self.assertRaises(ConflictError):
            flow.handoff(sid, session['revision'], 'ready')
        self.assertIsNone(flow.show(sid)['approval'])
        self.assertFalse(flow.show(sid)['ready_for_document_review'])

    @unittest.skipUnless(os.environ.get('UDH_TEST_OPENSPEC') and os.environ.get('UDH_TEST_DISCOVERY_PI') == '1',
                         'Pi local e OpenSpec não foram selecionados para o ensaio')
    def test_real_pi_with_loopback_test_server(self):
        self.core.migrate(9)
        with self.server([{'gaps': []}, DRAFT]) as (port, calls):
            flow = self.flow(port)
            session = flow.create('sample', 'Restaurar a última tela quando o app reiniciar.')
            sid = session['session_id']
            session = flow.discover(sid, 0)
            self.assertTrue(session['ready_for_document_review'])
            session = flow.draft(sid, session['revision'])
            session = flow.approve(sid, session['revision'], session['snapshot_sha256'], 'human-approval')
            self.assertEqual(flow.handoff(sid, session['revision'], 'ready')['status'], 'SPEC_READY')
            self.assertEqual(len(calls), 2)

    @unittest.skipUnless(os.environ.get('UDH_TEST_OPENSPEC'), 'CLI real não configurada')
    def test_loopback_to_spec_ready_and_replay(self):
        self.core.migrate(9)
        legacy = self.core.create('legacy', '1', 'create')
        with self.server([{'gaps': [GAP]}, DRAFT]) as (port, calls), patch(
                'udh.discovery_flow.PiAdapter.run', side_effect=self.pi_boundary):
            flow = self.flow(port)
            session = flow.create('sample', 'Reabrir o aplicativo.')
            sid = session['session_id']
            session = flow.discover(sid, 0)
            with self.assertRaises(ConflictError):
                flow.draft(sid, session['revision'])
            answer = 'Restaurar a última tela.'
            session = flow.answer(sid, session['revision'], 'restart', answer)
            session = flow.confirm(sid, session['revision'], 'restart', hashlib.sha256(answer.encode()).hexdigest())
            session = flow.draft(sid, session['revision'])
            with self.assertRaises(ConflictError):
                flow.handoff(sid, session['revision'], 'ready')
            with self.assertRaises(ConflictError):
                flow.approve(sid, session['revision'], '0' * 64, 'human-approval')
            approved = flow.approve(sid, session['revision'], session['snapshot_sha256'], 'human-approval')
            ready = flow.handoff(sid, approved['revision'], 'ready')
            self.assertEqual(ready['status'], 'SPEC_READY')
            # Reabrir o banco preserva estado e o replay exato.
            self.core = CoreService(SQLiteStore(self.root / 'app.db'))
            self.assertEqual(self.flow(port).handoff(sid, approved['revision'], 'ready'), ready)
            self.assertEqual(self.core.show(legacy.run_id), legacy)
        self.assertEqual(len(calls), 2)
        for path, body, auth in calls:
            self.assertEqual(path, '/v1/chat/completions')
            self.assertEqual(body['model'], 'test-model')
            self.assertIsNone(auth)
            self.assertNotIn('tools', body)

    def test_discovery_flow_opencode_zen_free_backend(self):
        self.core.migrate(9)
        cli = Path(os.environ.get('UDH_TEST_OPENSPEC', '/missing/openspec'))

        # 1. Retrocompatibilidade posicional preservada + rejeições fail-closed de DiscoveryConfig
        pos_cfg = DiscoveryConfig(SKILL, 11434, 'local-model', self.trust, cli, cli.parent)
        self.assertEqual(pos_cfg.backend, 'local_discovery')
        for bad_kwargs in (
            dict(skill_path=SKILL, port=11434, model='deepseek-v4.1-flash-free', trust_store=self.trust,
                 cli_path=cli, node_dir=cli.parent, backend='opencode_zen_free', secret_resolver=lambda: 'public'),
            dict(skill_path=SKILL, port=None, model='paid-model', trust_store=self.trust,
                 cli_path=cli, node_dir=cli.parent, backend='opencode_zen_free', secret_resolver=lambda: 'public'),
            dict(skill_path=SKILL, port=None, model='deepseek-v4.1-flash-free', trust_store=self.trust,
                 cli_path=cli, node_dir=cli.parent, backend='opencode_zen_free', secret_resolver=None),
            dict(skill_path=SKILL, port=11434, model='local-model', trust_store=self.trust,
                 cli_path=cli, node_dir=cli.parent, backend='local_discovery', timeout_seconds=True),
            dict(skill_path=SKILL, port=11434, model='local-model', trust_store=self.trust,
                 cli_path=cli, node_dir=cli.parent, backend='unknown'),
            dict(skill_path=SKILL, port=11434, model='local-model', trust_store=self.trust,
                 cli_path=cli, node_dir=cli.parent, backend='local_discovery', connection_factory=lambda: None),
        ):
            with self.subTest(bad_kwargs=bad_kwargs.get('backend')), self.assertRaises(DiscoveryFlowError):
                DiscoveryConfig(**bad_kwargs)

        # 2. --use-env-api-key falha fechado sem variáveis; --summary-out bloqueia pastas de produção/.git
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(DiscoveryFlowError):
            _resolve_zen_secret(use_env_api_key=True)
        repo_root = Path(__file__).resolve().parent.parent
        for forbidden in ('udh/out.json', 'runtime/out.json', 'tests/out.json', 'bootstrap/out.json', '.git/out.json'):
            with self.subTest(forbidden=forbidden), self.assertRaises(DiscoveryFlowError):
                _validate_summary_out_path(repo_root / forbidden, repo_root=repo_root)

        # 3. Fluxo completo discovery -> draft -> approve -> handoff com backend opencode_zen_free
        catalog = json.dumps({'data': [{'id': 'deepseek-v4.1-flash-free'}]}).encode('utf-8')
        r1 = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps({'gaps': []}), 'function_call': None,
        }}]}).encode('utf-8')
        r2 = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(DRAFT), 'function_call': None,
        }}]}).encode('utf-8')
        responses = [
            _FakeHTTPResponse(200, catalog), _FakeHTTPResponse(200, r1),
            _FakeHTTPResponse(200, catalog), _FakeHTTPResponse(200, r2),
        ]
        seen_pi = []

        def fake_pi(**kwargs):
            seen_pi.append((kwargs['max_output_tokens'], kwargs['limits'].timeout_seconds, kwargs['gateway'].config.backend))
            return self.pi_boundary(**kwargs)

        cfg = DiscoveryConfig.for_opencode_zen_free(
            skill_path=SKILL,
            model='deepseek-v4.1-flash-free',
            trust_store=self.trust,
            cli_path=cli,
            node_dir=cli.parent,
            connection_factory=lambda: _FakeHTTPSConnection([responses.pop(0)]),
            timeout_seconds=60,
        )
        flow = self.core.discovery_flow(cfg)
        with patch('udh.discovery_flow.PiAdapter.run', side_effect=fake_pi):
            s = flow.create('zen-sample', 'Restaurar a última tela ao reiniciar o app.')
            s = flow.discover(s['session_id'], s['revision'])
            self.assertTrue(s['ready_for_document_review'])
            s = flow.draft(s['session_id'], s['revision'])
            s = flow.approve(s['session_id'], s['revision'], s['snapshot_sha256'], 'human-zen')
            if os.environ.get('UDH_TEST_OPENSPEC'):
                ready = flow.handoff(s['session_id'], s['revision'], 'req-zen-ready')
                self.assertEqual(ready['status'], 'SPEC_READY')
            else:
                with patch('udh.discovery_flow.validate_spec_snapshot', return_value=SpecValidationResult(
                    'zen-sample', s['approval']['revision'], s['snapshot_sha256'], 'VALIDATED', '2.1.1',
                )):
                    ready = flow.handoff(s['session_id'], s['revision'], 'req-zen-ready')
                    self.assertEqual(ready['status'], 'SPEC_READY')
        # Ambas as fases usam max_output_tokens=2048 e timeout de contêiner = 60 + 15 = 75 s
        self.assertEqual(seen_pi, [
            (2048, 75, 'opencode_zen_free'),
            (2048, 75, 'opencode_zen_free'),
        ])

        # 4. PiAdapter aceita opencode_zen_free com max_output_tokens=2048 e propaga timeout <= 120000 ms
        from udh.adapters.pi import PiAdapter
        from udh.runtime import ContainerResult

        captured_pi = {}

        class _CaptureRuntime:
            def load_manifest(self):
                return SimpleNamespace(
                    node_bin='/opt/udh/bin/node', pi_entrypoint='/opt/pi/cli.js',
                    bridge_path='/opt/udh/bridge.mjs', image_ref='localhost/udh-runtime@sha256:' + 'e' * 64,
                )

            def run_developer(self, *, mounts, env, **kwargs):
                settings_mount = next(m for m in mounts if m.destination == '/udh/agent-config/settings.json')
                captured_pi['settings'] = json.loads(Path(settings_mount.source).read_text(encoding='utf-8'))
                captured_pi['env'] = dict(env)
                return ContainerResult(
                    container_name='cap', profile='developer', image_ref=kwargs['image_ref'], returncode=0,
                    stdout=(
                        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"{\\"gaps\\":[]}"}],"stopReason":"stop"}}\n'
                        '{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop"}]}\n'
                    ),
                    stderr='', outcome=None, elapsed_seconds=0.01, inspection={}, cleanup_verified=True,
                )

        pi_gw_cfg = GatewayConfig(
            run_id='zen-pi-check', policy_sha256='0' * 64, audit_path=self.root / 'pi-zen.jsonl',
            backend='opencode_zen_free', allowed_models=('deepseek-v4.1-flash-free',),
            secret_resolver=lambda: 'public', call_timeout_seconds=60,
            opencode_zen_connection_factory=lambda: _FakeHTTPSConnection([_FakeHTTPResponse(200, catalog)]),
        )
        ws_pi = self.root / 'ws-pi'
        ws_pi.mkdir()
        with InferenceGateway(pi_gw_cfg) as pi_gw:
            pi_res = PiAdapter(runtime_manager=_CaptureRuntime()).run(
                workspace=ws_pi, gateway=pi_gw, prompt='hi', model='deepseek-v4.1-flash-free',
                allowed_tools=(), max_output_tokens=2048,
            )
        self.assertTrue(pi_res.ok)
        self.assertEqual(captured_pi['settings']['httpIdleTimeoutMs'], 70000)
        self.assertEqual(captured_pi['settings']['retry']['provider']['timeoutMs'], 70000)
        self.assertEqual(captured_pi['env']['UDH_GATEWAY_TIMEOUT_MS'], '70000')

        # 5. Runner interativo run_opencode_zen_trial gera resumo compacto <= 32 KiB fora de pastas protegidas
        out_dir = tempfile.TemporaryDirectory(dir='/tmp', prefix='udh-zen-out-')
        self.addCleanup(out_dir.cleanup)
        summary_path = Path(out_dir.name) / 'zen-trial-summary.json'
        trial_responses = [
            _FakeHTTPResponse(200, catalog), _FakeHTTPResponse(200, r1),
            _FakeHTTPResponse(200, catalog), _FakeHTTPResponse(200, r2),
            _FakeHTTPResponse(200, catalog), _FakeHTTPResponse(200, json.dumps({'choices': [{
                'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({'gaps': [GAP]})},
            }]}).encode('utf-8')),
            _FakeHTTPResponse(200, catalog), _FakeHTTPResponse(200, r2),
        ]
        def trial_pi(**kwargs):
            self.assertEqual(kwargs['allowed_tools'], ())
            self.assertEqual(len(kwargs['forbidden_rw_roots']), 1)
            self.assertEqual(kwargs['forbidden_rw_roots'][0].name, 'trust-store')
            client = _UDSConnection(kwargs['gateway'].socket_path)
            try:
                body = {'model': kwargs['model'], 'stream': True, 'messages': [
                    {'role': 'system', 'content': kwargs['system_prompt']}, {'role': 'user', 'content': kwargs['prompt']}]}
                client.request('POST', '/v1/chat/completions', json.dumps(body).encode(),
                               {'Authorization': 'Bearer ' + kwargs['gateway'].capability_token,
                                'Content-Type': 'application/json', 'X-UDH-Purpose': 'inference'})
                response = client.getresponse()
                raw = response.read().decode()
            finally:
                client.close()
            text = ''.join(json.loads(line[6:])['choices'][0]['delta'].get('content', '')
                           for line in raw.splitlines() if line.startswith('data: {'))
            return SimpleNamespace(ok=response.status == 200 and '[DONE]' in raw, assistant_text=text)

        answers = iter(['s', 'Restaurar a última tela ao abrir.', 's'])
        with patch('udh.discovery_flow.PiAdapter.run', side_effect=trial_pi), patch(
            'udh.discovery_flow.validate_spec_snapshot',
            side_effect=lambda cid, rev, art, *, approval, **kw: SpecValidationResult(cid, rev, approval[2], 'VALIDATED', '2.1.1'),
        ):
            summary = run_opencode_zen_trial(
                model='deepseek-v4.1-flash-free',
                summary_out=summary_path,
                cli_path=cli,
                skill_path=SKILL,
                input_fn=lambda _prompt: next(answers),
                print_fn=lambda _msg: None,
                connection_factory=lambda: _FakeHTTPSConnection([trial_responses.pop(0)]),
            )
        self.assertEqual(summary['verdict'], 'PASS')
        self.assertEqual(summary['auth_mode'], 'public')
        self.assertTrue(summary['cases']['complete_idea']['ok'])
        self.assertTrue(summary['cases']['material_gap']['ok'])
        self.assertTrue(summary['cases']['material_gap']['insufficient_answer_blocked'])
        self.assertLessEqual(summary_path.stat().st_size, 32768)
