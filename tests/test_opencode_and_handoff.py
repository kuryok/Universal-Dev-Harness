"""Testes unitários e de integração (Podman rootless) do Change 3: Gateway allowlist, OpenCode e Handoff F-03."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

from udh.adapters.opencode import (
    DEFAULT_OPENCODE_LIMITS,
    OpenCodeAdapter,
    parse_opencode_jsonl,
)
from udh.domain import CoreError
from udh.gateway import (
    DEFAULT_STUB_MODEL,
    GatewayConfig,
    GatewayError,
    InferenceGateway,
    build_openrouter_payload,
)
from udh.handoff import (
    HandoffError,
    build_handoff_packet,
    commit_handoff_snapshot,
    init_core_worktree,
    render_handoff_prompt,
    scan_worktree_physical,
    select_adapter,
    verify_handoff_packet,
)
from udh.policy import policy_v2_sha256
from udh.runtime import (
    DEFAULT_OPENCODE_MANIFEST_PATH,
    ResourceLimits,
    RuntimeIsolationError,
    RuntimeManager,
    load_opencode_manifest,
)
from udh.service import CoreService
from udh.store import SQLiteStore
from udh.trust_store import TrustStore, TrustStoreError


def _uds_request(sock_path: Path, raw: bytes) -> tuple[int, str]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(4.0)
        client.connect(str(sock_path))
        client.sendall(raw)
        chunks = []
        while True:
            piece = client.recv(8192)
            if not piece:
                break
            chunks.append(piece)
    text = b''.join(chunks).decode('utf-8', errors='replace')
    first = text.split('\r\n', 1)[0].split(' ')
    return (int(first[1]) if len(first) >= 2 and first[1].isdigit() else 0), text


def _post_json(token: str, payload: dict) -> bytes:
    body = json.dumps(payload).encode('utf-8')
    headers = (
        'POST /v1/chat/completions HTTP/1.1\r\n'
        'Host: localhost\r\n'
        f'Authorization: Bearer {token}\r\n'
        'X-UDH-Purpose: inference\r\n'
        f'Content-Length: {len(body)}\r\n\r\n'
    ).encode('utf-8')
    return headers + body


def _opencode_image_ready() -> bool:
    try:
        mgr = RuntimeManager()
        manifest = mgr.load_opencode_manifest()
        mgr.verify_image_present(manifest.image_ref)
        return True
    except (RuntimeIsolationError, CoreError, OSError):
        return False


class TestGatewayToolAllowlistAndStubTurns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='udh-c3-gw-')
        self.root = Path(self.tmp.name)
        self.audit = self.root / 'audit.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowed_tool_names_fail_closed_and_legacy_functions_denied(self):
        cfg = GatewayConfig(
            run_id='run-gw-tools',
            policy_sha256='a' * 64,
            audit_path=self.audit,
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=True,
            allowed_tool_names=('read', 'write'),
        )
        with InferenceGateway(cfg) as gw:
            # Tool permitida -> 200
            st_ok, _ = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [{'type': 'function', 'function': {'name': 'read'}}],
            }))
            self.assertEqual(st_ok, 200)

            # Tools ausente e [] continuam permitidos com allow_tools=True
            st_empty, _ = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [],
            }))
            self.assertEqual(st_empty, 200)

            # Tool fora da allowlist (webfetch) -> 403 tools_denied
            st_deny, txt_deny = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [{'type': 'function', 'function': {'name': 'webfetch'}}],
            }))
            self.assertEqual(st_deny, 403)
            self.assertIn('tools_denied', txt_deny)

            # Campo legado functions -> 403 tools_denied
            st_legacy, _ = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'hi'}],
                'functions': [{'name': 'read'}],
            }))
            self.assertEqual(st_legacy, 403)

            # tools não-lista ({}, "") ou malformado -> 400 tools_invalid
            for bad_tools in ({}, '', [{'type': 'other'}], [{'type': 'function', 'function': {'name': ''}}]):
                st_bad, txt_bad = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                    'model': DEFAULT_STUB_MODEL, 'stream': True,
                    'messages': [{'role': 'user', 'content': 'hi'}],
                    'tools': bad_tools,
                }))
                self.assertEqual(st_bad, 400)
                self.assertIn('tools_invalid', txt_bad)

        lines = [json.loads(line) for line in self.audit.read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertTrue(any(rec.get('tool_names') == ['webfetch'] and rec.get('status') == 403 for rec in lines))
        self.assertTrue(any(rec.get('tool_names') == ['read'] and rec.get('status') == 200 for rec in lines))

    def test_allow_tools_true_with_empty_allowlist_fails_closed(self):
        cfg = GatewayConfig(
            run_id='run-gw-empty-allow',
            policy_sha256='a' * 64,
            audit_path=self.audit,
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=True,
            allowed_tool_names=(),
        )
        with InferenceGateway(cfg) as gw:
            st, _ = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [{'type': 'function', 'function': {'name': 'read'}}],
            }))
            self.assertEqual(st, 403)

    def test_build_openrouter_payload_strict_no_silent_strip(self):
        with self.assertRaises(GatewayError):
            build_openrouter_payload(
                {
                    'messages': [{'role': 'user', 'content': 'hi'}],
                    'tools': [
                        {'type': 'function', 'function': {'name': 'read'}},
                        {'type': 'function', 'function': {'name': 'webfetch'}},
                    ],
                },
                allowed_model='m:free',
                allow_tools=True,
                allowed_tool_names=('read',),
            )
        rebuilt = build_openrouter_payload(
            {
                'messages': [{'role': 'user', 'content': 'hi'}],
                'tools': [{
                    'type': 'function',
                    'extra_ignored': True,
                    'function': {'name': 'read', 'description': 'Read file', 'parameters': {'type': 'object'}, 'evil': 1},
                }],
            },
            allowed_model='m:free',
            allow_tools=True,
            allowed_tool_names=('read',),
        )
        self.assertEqual(
            rebuilt['tools'],
            [{'type': 'function', 'function': {'name': 'read', 'description': 'Read file', 'parameters': {'type': 'object'}}}],
        )

    def test_stub_turns_separates_auxiliary_no_tools_from_tool_calls(self):
        cfg = GatewayConfig(
            run_id='run-gw-turns',
            policy_sha256='a' * 64,
            audit_path=self.audit,
            backend='stub',
            allowed_models=(DEFAULT_STUB_MODEL,),
            allow_tools=True,
            allowed_tool_names=('write',),
            stub_response_text='AUX_TITLE_OK',
            stub_turns=(
                {'require_tools': True, 'tool_calls': [{'id': 'c1', 'name': 'write', 'arguments': {'path': 'a.py'}}]},
                {'require_tools': True, 'text': 'MAIN_DONE'},
            ),
        )
        with InferenceGateway(cfg) as gw:
            # Chamada auxiliar sem tools não consome o turno tool_calls
            st_aux, txt_aux = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'title'}],
            }))
            self.assertEqual(st_aux, 200)
            self.assertIn('AUX_TI', txt_aux)
            self.assertIn('TLE_OK', txt_aux)

            # Turno principal com tools recebe tool_calls
            st_t1, txt_t1 = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'code'}],
                'tools': [{'type': 'function', 'function': {'name': 'write'}}],
            }))
            self.assertEqual(st_t1, 200)
            self.assertIn('tool_calls', txt_t1)
            self.assertIn('c1', txt_t1)

            # Turno 2 com tools recebe MAIN_DONE
            st_t2, txt_t2 = _uds_request(gw.socket_path, _post_json(gw.capability_token, {
                'model': DEFAULT_STUB_MODEL, 'stream': True,
                'messages': [{'role': 'user', 'content': 'after tool'}],
                'tools': [{'type': 'function', 'function': {'name': 'write'}}],
            }))
            self.assertEqual(st_t2, 200)
            self.assertIn('MAIN', txt_t2)
            self.assertIn('_DONE', txt_t2)


class TestOpenCodeManifestAndParser(unittest.TestCase):
    def test_load_opencode_manifest_detects_stale_bridge(self):
        with tempfile.TemporaryDirectory(prefix='udh-oc-man-') as tmp:
            root = Path(tmp)
            (root / 'bridge.mjs').write_text('console.log("changed");\n', encoding='utf-8')
            man = root / 'opencode-image-manifest.json'
            man.write_text(json.dumps({
                'schema_version': 1,
                'adapter_name': 'opencode',
                'stack_profile': 'python3-stdlib-v1',
                'image_repository': 'localhost/udh-runtime-opencode',
                'image_digest': 'sha256:' + 'a' * 64,
                'image_ref': 'localhost/udh-runtime-opencode@sha256:' + 'a' * 64,
                'opencode_version': '1.18.35',
                'opencode_bin': '/opt/udh/bin/opencode',
                'node_bin': '/opt/udh/bin/node',
                'bridge_path': '/opt/udh/bridge.mjs',
                'bridge_sha256': 'b' * 64,
            }), encoding='utf-8')
            with self.assertRaises(RuntimeIsolationError) as ctx:
                load_opencode_manifest(man, verify_local_sources=True)
            self.assertIn('IMAGE_DIGEST_STALE', str(ctx.exception))

    def test_parse_opencode_jsonl_strict(self):
        ok_stream = (
            '{"type":"step_start","part":{"type":"step-start"}}\n'
            '{"type":"text","part":{"type":"text","text":"HELLO"}}\n'
            '{"type":"step_finish","part":{"type":"step-finish","reason":"stop"}}\n'
        )
        parsed = parse_opencode_jsonl(ok_stream)
        self.assertEqual(parsed['malformed_lines'], 0)
        self.assertEqual(parsed['last_stop_reason'], 'stop')
        self.assertFalse(parsed['has_error_event'])
        self.assertEqual(parsed['assistant_text'], 'HELLO')

        err_stream = (
            '{"type":"step_start","part":{"type":"step-start"}}\n'
            '{"type":"error","error":{"name":"APIError","message":"cut"}}\n'
        )
        parsed_err = parse_opencode_jsonl(err_stream)
        self.assertTrue(parsed_err['has_error_event'])
        self.assertIsNone(parsed_err['last_stop_reason'])


class TestAdapterSelectorAndHandoffUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='udh-c3-handoff-')
        self.root = Path(self.tmp.name)
        self.store = TrustStore(self.root / 'store')
        self.db_path = self.root / 'core.db'
        self.svc = CoreService(SQLiteStore(self.db_path))
        self.svc.initialize(schema_version=4)
        self.git_dir = self.root / 'core-git'
        self.ws = self.root / 'ws'
        self.ws.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_select_adapter_deterministic_adr026(self):
        sel_primary = select_adapter(('opencode', 'pi'))
        self.assertEqual(sel_primary['selected_adapter'], 'pi')
        self.assertFalse(sel_primary['fallback_used'])

        sel_fb = select_adapter(('opencode',), preference_order=('pi', 'opencode'))
        self.assertEqual(sel_fb['selected_adapter'], 'opencode')
        self.assertTrue(sel_fb['fallback_used'])

        sel_ex = select_adapter(('pi', 'opencode'), exclude=('pi',))
        self.assertEqual(sel_ex['selected_adapter'], 'opencode')
        self.assertTrue(sel_ex['fallback_used'])

        with self.assertRaises(HandoffError) as ctx_none:
            select_adapter(())
        self.assertEqual(ctx_none.exception.reason_code, 'NO_CONFORMANT_ADAPTER')

        with self.assertRaises(HandoffError) as ctx_req:
            select_adapter(('pi',), requested='opencode')
        self.assertEqual(ctx_req.exception.reason_code, 'REQUESTED_ADAPTER_NOT_CONFORMANT')

    def test_git_dir_inside_workspace_and_forbidden_worktree_entries_rejected(self):
        (self.ws / 'app.py').write_text('x = 1\n', encoding='utf-8')
        with self.assertRaises(HandoffError):
            init_core_worktree(self.ws / '.git', self.ws)

        init_core_worktree(self.git_dir, self.ws)

        # .git, .gitattributes, symlink e shadowing rejeitados fisicamente
        for bad_name in ('.git', '.gitattributes', '.gitmodules', 'sitecustomize.py', 'evil.pth'):
            bad_path = self.ws / bad_name
            bad_path.write_text('bad\n', encoding='utf-8')
            with self.assertRaises(HandoffError) as ctx:
                scan_worktree_physical(self.ws)
            self.assertEqual(ctx.exception.reason_code, 'HANDOFF_INTEGRITY_VIOLATION')
            bad_path.unlink()

        link = self.ws / 'link.py'
        link.symlink_to('/etc/passwd')
        with self.assertRaises(HandoffError):
            scan_worktree_physical(self.ws)
        link.unlink()

    def test_handoff_fail_closed_evidence_tampering_and_conversational_keys(self):
        (self.ws / 'calc.py').write_text('print(1)\n', encoding='utf-8')
        base = init_core_worktree(self.git_dir, self.ws)
        (self.ws / 'calc.py').write_text('print(2)\n', encoding='utf-8')
        head = commit_handoff_snapshot(self.git_dir, self.ws)

        run = self.svc.create('spec-1', '1', 'req-h-create', ticket_id='T-1', mode='STANDARD')
        self.svc.start(run.run_id, 0, 'req-h-start')

        ev_report = {'verdict': 'FAIL', 'tests_run': 1, 'failures': 1}
        ev_path, ev_sha = self.store.save_evidence(run.run_id, 'req-ev-1', ev_report)
        self.svc.record_evidence(
            run.run_id, 'req-ev-1',
            kind='AT_RESULT', verdict='FAIL',
            artifact_digest='a' * 64, evidence_sha256=ev_sha,
            collector_route='TRUSTED_EXECUTOR', summary_ref=str(ev_path),
            at_version='sha256:' + 'b' * 64,
        )
        self.svc.record_checkpoint(
            run.run_id, expected_revision=1, request_id='req-ckpt-1',
            last_confirmed_step='pi_done',
            base_commit_sha=base['base_commit_sha'],
            head_commit_sha=head['head_commit_sha'],
            worktree_snapshot_sha256=head['worktree_snapshot_sha256'],
            evidence_hashes=(ev_sha,),
            unknown_pendencies=(),
        )

        # Pacote válido passa
        pkt = build_handoff_packet(
            core_service=self.svc, trust_store=self.store, run_id=run.run_id,
            git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
            objective='Corrigir calc.py', acceptance_criteria=('calc.py imprime 3',),
            allowed_tools=('read', 'write'), conformant_adapters=('pi', 'opencode'),
        )
        self.assertEqual(pkt['to_adapter'], 'opencode')
        prompt = render_handoff_prompt(pkt)
        self.assertIn(pkt['packet_sha256'], prompt)

        # Adulteração de packet_sha256 falha
        tampered = dict(pkt)
        tampered['from_adapter'] = 'other'
        with self.assertRaises(HandoffError):
            verify_handoff_packet(tampered)

        # Chave conversacional proibida falha
        conv_bad = dict(pkt)
        conv_bad['task_contract'] = {**pkt['task_contract'], 'transcript': 'secret'}
        with self.assertRaises(HandoffError):
            verify_handoff_packet(conv_bad)

        # Adulteração física na TrustStore falha com HANDOFF_EVIDENCE_MISMATCH
        os.chmod(ev_path, 0o644)
        ev_path.write_text('{"verdict":"PASS"}\n', encoding='utf-8')
        with self.assertRaises(HandoffError) as ctx_ev:
            build_handoff_packet(
                core_service=self.svc, trust_store=self.store, run_id=run.run_id,
                git_dir=self.git_dir, workspace=self.ws, from_adapter='pi', to_adapter='opencode',
                objective='Corrigir calc.py', acceptance_criteria=('calc.py imprime 3',),
                allowed_tools=('read', 'write'), conformant_adapters=('pi', 'opencode'),
            )
        self.assertEqual(ctx_ev.exception.reason_code, 'HANDOFF_EVIDENCE_MISMATCH')

    def test_trust_store_adapter_name_regex_and_spoofing_protection(self):
        with self.assertRaises(TrustStoreError):
            self.store.save_conformance(
                policy_sha256='a' * 64,
                image_digest='sha256:' + 'b' * 64,
                adapter_name='../evil',
                adapter_version='1.0',
                status='PASS',
            )
        self.store.save_conformance(
            policy_sha256='a' * 64,
            image_digest='sha256:' + 'b' * 64,
            adapter_name='opencode',
            adapter_version='1.18.35',
            status='PASS',
        )
        loaded = self.store.load_conformance(
            policy_sha256='a' * 64,
            image_digest='sha256:' + 'b' * 64,
            adapter_name='opencode',
        )
        self.assertEqual(loaded['status'], 'PASS')
        # Spoofing de adapter_name dentro do arquivo -> STALE
        p = self.store.root / 'conformance-opencode.json'
        data = json.loads(p.read_text(encoding='utf-8'))
        data['adapter_name'] = 'pi'
        p.write_text(json.dumps(data), encoding='utf-8')
        spoofed = self.store.load_conformance(
            policy_sha256='a' * 64,
            image_digest='sha256:' + 'b' * 64,
            adapter_name='opencode',
        )
        self.assertEqual(spoofed['status'], 'STALE')


@unittest.skipUnless(_opencode_image_ready(), 'Imagem OCI OpenCode ou Podman rootless indisponível')
class TestOpenCodeAdapterPodmanIntegration(unittest.TestCase):
    def test_opencode_conformance_e3_zero_tools_e4_hostile_config_and_gateway_cut(self):
        mgr = RuntimeManager()
        adapter = OpenCodeAdapter(runtime_manager=mgr)
        pol_sha = policy_v2_sha256()

        with tempfile.TemporaryDirectory(prefix='udh-oc-int-') as tmp:
            root = Path(tmp)
            store = TrustStore(root / 'store')

            # 1. Conformance real E3 zero_tools com allow_tools=False
            conf = adapter.run_conformance(trust_store=store, policy_sha256=pol_sha)
            self.assertTrue(conf.ok, conf.details)
            self.assertEqual(conf.details['conformance_tool_mode'], 'zero_tools')
            self.assertEqual(conf.details['observed_tool_names'], [])
            self.assertTrue(conf.config_integrity_ok)

            # 2. Controle negativo E4: /workspace/opencode.json hostil ignorado
            ws = root / 'ws'
            ws.mkdir()
            (ws / 'opencode.json').write_text(json.dumps({
                'autoupdate': True,
                'share': 'manual',
                'plugin': ['evil'],
                'tools': {'webfetch': True, 'bash': True},
                'permission': {'*': 'allow'},
            }), encoding='utf-8')
            inspected = adapter.inspect_effective_config(workspace=ws, profile='conformance')
            self.assertTrue(inspected['config_integrity_ok'])
            self.assertFalse(inspected['config']['autoupdate'])
            self.assertEqual(inspected['config']['share'], 'disabled')
            self.assertEqual(inspected['config']['plugin'], [])
            self.assertFalse(inspected['config']['tools']['webfetch'])

            # 3. Corte abrupto do gateway detectado fail-closed (ok=False mesmo sem erro explícito de processo)
            gw_cut_cfg = GatewayConfig(
                run_id='run-oc-cut',
                policy_sha256=pol_sha,
                audit_path=root / 'audit-cut.jsonl',
                backend='stub',
                allowed_models=(DEFAULT_STUB_MODEL,),
                allow_tools=False,
                cut_after_first_chunk=True,
                max_calls=4,
            )
            with InferenceGateway(gw_cut_cfg) as gw:
                cut_res = adapter.run(
                    workspace=ws,
                    gateway=gw,
                    profile='conformance',
                    prompt='Teste corte.',
                    limits=ResourceLimits(memory_mb=768, pids_limit=128, cpus=1.0, timeout_seconds=15.0),
                )
            self.assertFalse(cut_res.ok)
            self.assertIn(
                cut_res.reason_code,
                ('AGENT_STREAM_INTERRUPTED', 'AGENT_NONZERO_EXIT', 'AGENT_INCOMPLETE_STOP_REASON', 'CONTAINER_TIMEOUT'),
            )


if __name__ == '__main__':
    unittest.main()
