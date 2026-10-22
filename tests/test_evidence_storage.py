"""Complementares sintéticos do codec/store; não executam Core ou AT históricos."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / 'bootstrap/slice1'
sys.path.insert(0, str(BASE))
import evidence_store as evidence


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def compressed(raw):
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', compresslevel=9, mtime=0, fileobj=buffer) as stream:
        stream.write(raw)
    return buffer.getvalue()


def reference(kind, raw, stored):
    return dict(schema=1, kind=kind, codec='json-gzip-v1',
                stored_sha256=hashlib.sha256(stored).hexdigest(), stored_bytes=len(stored),
                json_sha256=hashlib.sha256(raw).hexdigest(), json_bytes=len(raw))


class EvidenceCodecTests(unittest.TestCase):
    def raw_case(self, raw, kind='audit'):
        stored = compressed(raw)
        return reference(kind, raw, stored), stored

    def leaves(self):
        return dict(audit=evidence.encode('audit', [{'literal': ' ç\n espaço ', 'n': 1.0}])[0],
                    transcript=evidence.encode('transcript', ' stdout\n\tstderr ')[0])

    def test_roundtrip_literal_and_exact_envelope_reference(self):
        payload = [{'texto': ' ç\n\t espaço ', 'inteiro': 1, 'real': 1.0,
                    'lista': [None, True, False, -0.0], 'path': '/fora/do/projeto/.env'}]
        ref, stored = evidence.encode('audit', payload)
        expected = dict(schema=1, kind='audit', payload=payload, links={})
        raw = canonical(expected)
        self.assertEqual(ref, reference('audit', raw, stored))
        self.assertEqual(evidence.decode(ref, stored), expected)
        self.assertEqual(gzip.decompress(stored), raw)
        self.assertEqual(stored, compressed(raw))
        self.assertEqual(evidence.encode('audit', payload), (ref, stored))
        self.assertEqual(stored[3] & 8, 0)  # Sem filename no cabeçalho gzip.
        self.assertEqual(stored[4:8], b'\0\0\0\0')

    def test_closed_kinds_and_link_types(self):
        leaves = self.leaves()
        report = evidence.encode('report', {'status': 'FAIL'}, leaves)[0]
        regression = evidence.encode('regression', {'status': 'PASS'}, {'v1': report})[0]
        cases = [('audit', [], {}), ('transcript', '', {}),
                 ('report', {}, leaves), ('report', {}, dict(leaves, regression=regression)),
                 ('regression', {}, {'v1': report, 'outro_2': report}),
                 ('review', {}, {'baseline': report, 'regression': regression})]
        for kind, payload, links in cases:
            with self.subTest(kind=kind, links=links):
                ref, stored = evidence.encode(kind, payload, links)
                self.assertEqual(evidence.decode(ref, stored),
                                 dict(schema=1, kind=kind, payload=payload, links=links))

    def test_invalid_kind_payload_or_links_rejected(self):
        leaves = self.leaves()
        report = evidence.encode('report', {}, leaves)[0]
        regression = evidence.encode('regression', {}, {'v1': report})[0]
        cases = [('unknown', {}, {}), ('audit', {}, {}), ('audit', [1], {}),
                 ('audit', [], leaves), ('transcript', [], {}), ('transcript', 'ok', leaves),
                 ('report', [], leaves), ('report', {}, {}),
                 ('report', {}, {'audit': leaves['audit']}),
                 ('report', {}, dict(leaves, extra=report)),
                 ('report', {}, dict(leaves, audit=leaves['transcript'])),
                 ('regression', {}, {}), ('regression', {}, {'Upper': report}),
                 ('regression', {}, {'a' * 65: report}), ('regression', {}, {'../x': report}),
                 ('regression', {}, {'a': regression}),
                 ('review', {}, {'baseline': report}),
                 ('review', {}, {'baseline': regression, 'regression': report}),
                 ('review', {}, {'baseline': report, 'regression': regression, 'extra': report})]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(evidence.EvidenceError):
                evidence.encode(*args)

    def test_non_json_values_keys_numbers_and_excessive_depth_rejected(self):
        nested = None
        for _ in range(70):
            nested = [nested]
        values = [{1: 'key'}, {'value': (1, 2)}, {'value': {1, 2}}, {'value': b'bytes'},
                  {'value': object()}, {'value': math.nan}, {'value': math.inf},
                  {'value': -math.inf}, {'value': nested}]
        for value in values:
            with self.subTest(value=type(value)), self.assertRaises(evidence.EvidenceError):
                evidence.encode('audit', [value])

    def test_reference_shape_types_hashes_and_sizes_rejected(self):
        ref, stored = evidence.encode('audit', [])
        changes = [('schema', True), ('schema', 2), ('kind', 'transcript'), ('codec', 'pickle'),
                   ('stored_sha256', 'A' * 64), ('stored_sha256', '../file'),
                   ('json_sha256', '0' * 64), ('stored_bytes', True), ('json_bytes', False),
                   ('stored_bytes', 0), ('json_bytes', -1), ('json_bytes', 1.0), ('schema', 1.0), ('stored_bytes', len(stored) + 1),
                   ('json_bytes', ref['json_bytes'] + 1)]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(evidence.EvidenceError):
                evidence.decode(dict(ref, **{key: value}), stored)
        for bad in (dict(ref, path='/tmp/obj'), {k: v for k, v in ref.items() if k != 'kind'}, [], None):
            with self.subTest(bad=bad), self.assertRaises(evidence.EvidenceError):
                evidence.decode(bad, stored)

    def test_gzip_crc_truncation_trailing_and_concatenated_members(self):
        ref, stored = evidence.encode('audit', [])
        bad_crc = stored[:-8] + bytes([stored[-8] ^ 1]) + stored[-7:]
        for mutated in (stored[:-1], stored[:10], bad_crc, stored + b'trailing',
                        stored + compressed(b'{}'), b'not gzip'):
            bad_ref = dict(ref, stored_sha256=hashlib.sha256(mutated).hexdigest(), stored_bytes=len(mutated))
            with self.subTest(size=len(mutated)), self.assertRaises(evidence.EvidenceError):
                evidence.decode(bad_ref, mutated)

    def test_duplicate_nonfinite_noncanonical_and_malformed_json_rejected(self):
        raws = [b'{"kind":"audit","kind":"audit","links":{},"payload":[],"schema":1}',
                b'{"kind":"audit","links":{},"payload":[{"n":NaN}],"schema":1}',
                b'{"kind":"audit","links":{},"payload":[{"n":Infinity}],"schema":1}',
                b'{"kind":"audit","links":{},"payload":[{"n":1e999}],"schema":1}',
                b'{ "kind":"audit","links":{},"payload":[],"schema":1}',
                b'{"kind":"audit","links":{},"payload":[],"schema":1}\n',
                b'{"kind":"audit","links":{},"payload":[],"schema":true}',
                b'{"kind":"audit","links":{},"payload":[],"schema":1,"extra":0}',
                b'{"kind":"audit","links":{},"payload":[{"a":1,"a":2}],"schema":1}',
                b'\xff', b'{']
        for raw in raws:
            with self.subTest(raw=raw), self.assertRaises(evidence.EvidenceError):
                evidence.decode(*self.raw_case(raw))

    def test_declared_and_actual_individual_budgets(self):
        ref, stored = evidence.encode('transcript', 'x' * 1024)
        for constant, limit in (('MAX_STORED_BYTES', len(stored) - 1),
                                ('MAX_JSON_BYTES', ref['json_bytes'] - 1)):
            with self.subTest(constant=constant), patch.object(evidence, constant, limit):
                with self.assertRaises(evidence.EvidenceError):
                    evidence.decode(ref, stored)
                with self.assertRaises(evidence.EvidenceError):
                    evidence.encode('transcript', 'x' * 1024)
        # Uma declaração falsa pequena não dispensa medir a saída real do inflater.
        small = dict(ref, json_bytes=1)
        with patch.object(evidence, 'MAX_JSON_BYTES', 100):
            with self.assertRaises(evidence.EvidenceError):
                evidence.decode(small, stored)


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='evidence-storage-test-', dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.objects = self.root / 'objects'
        self.objects.mkdir()
        self.store = evidence.EvidenceStore(self.objects)

    def path(self, ref):
        return self.objects / (ref['stored_sha256'] + '.json.gz')

    def graph(self):
        audit = self.store.put('audit', [{'status': 'PASS'}])
        transcript = self.store.put('transcript', 'literal\nstdout stderr')
        report = self.store.put('report', {'status': 'FAIL'}, dict(audit=audit, transcript=transcript))
        regression = self.store.put('regression', {}, {'v1': report, 'same': report})
        review = self.store.put('review', {}, dict(baseline=report, regression=regression))
        return dict(audit=audit, transcript=transcript, report=report, regression=regression, review=review)

    def test_put_read_reuse_and_no_overwrite(self):
        ref = self.store.put('audit', [{'status': 'FAIL', 'stdout': 'inteiro'}])
        before = self.path(ref).read_bytes()
        inode = self.path(ref).stat().st_ino
        self.assertEqual(self.store.put('audit', [{'status': 'FAIL', 'stdout': 'inteiro'}]), ref)
        self.assertEqual(self.path(ref).stat().st_ino, inode)
        self.assertEqual(self.path(ref).read_bytes(), before)
        self.assertEqual(self.store.read(ref)['payload'], [{'status': 'FAIL', 'stdout': 'inteiro'}])
        self.assertEqual([path.name for path in self.objects.iterdir()], [self.path(ref).name])
        other = self.store.put('audit', [{'status': 'PASS'}])
        self.assertNotEqual(other['stored_sha256'], ref['stored_sha256'])
        self.assertEqual(self.path(ref).read_bytes(), before)

    def test_same_put_concurrent_writers_converge(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs = list(pool.map(lambda _: evidence.EvidenceStore(self.objects).put('transcript', 'mesmo'), range(8)))
        self.assertTrue(all(ref == refs[0] for ref in refs))
        self.assertEqual(len(list(self.objects.iterdir())), 1)
        self.assertEqual(self.store.read(refs[0])['payload'], 'mesmo')

    def test_missing_tampered_same_filename_and_existing_collision_refused(self):
        ref, stored = evidence.encode('audit', [])
        with self.assertRaises(evidence.EvidenceError):
            self.store.read(ref)
        self.path(ref).write_bytes(b'adulterado')
        with self.assertRaises(evidence.EvidenceError):
            self.store.read(ref)
        with self.assertRaises(evidence.EvidenceError):
            self.store.put('audit', [])
        self.assertEqual(self.path(ref).read_bytes(), b'adulterado')

    def test_root_must_exist_absolute_and_remain_in_project(self):
        for root in (Path('relative'), self.root / 'missing', Path('/'), Path('/tmp')):
            with self.subTest(root=str(root)), self.assertRaises(evidence.EvidenceError):
                evidence.EvidenceStore(root)
        ordinary = self.root / 'file'
        ordinary.write_text('not directory')
        with self.assertRaises(evidence.EvidenceError):
            evidence.EvidenceStore(ordinary)

    def test_symlink_root_ancestor_and_object_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.objects, target_is_directory=True)
        for root in (alias, alias / 'child'):
            if not (self.objects / 'child').exists():
                (self.objects / 'child').mkdir()
            with self.subTest(root=str(root)), self.assertRaises(evidence.EvidenceError):
                evidence.EvidenceStore(root)
        ref, stored = evidence.encode('audit', [])
        outside = self.root / 'regular'
        outside.write_bytes(stored)
        self.path(ref).symlink_to(outside)
        with self.assertRaises(evidence.EvidenceError):
            self.store.read(ref)
        with self.assertRaises(evidence.EvidenceError):
            self.store.put('audit', [])

    def test_fifo_object_is_refused_without_blocking(self):
        ref, _ = evidence.encode('audit', [])
        os.mkfifo(self.path(ref))
        with self.assertRaises(evidence.EvidenceError):
            self.store.read(ref)

    def test_read_uses_nofollow_nonblock_and_one_object_descriptor(self):
        ref = self.store.put('audit', [])
        real_open = os.open
        object_opens = []
        def observe(path, flags, *args, **kwargs):
            if Path(path).name == self.path(ref).name:
                object_opens.append(flags)
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_NONBLOCK)
            return real_open(path, flags, *args, **kwargs)
        with patch.object(evidence.os, 'open', side_effect=observe):
            self.assertEqual(self.store.read(ref)['payload'], [])
        self.assertEqual(len(object_opens), 1)

    def test_fsync_failure_before_publication_leaves_no_final_object(self):
        ref, _ = evidence.encode('audit', [])
        with patch.object(evidence.os, 'fsync', side_effect=OSError('falha sintética')):
            with self.assertRaises(evidence.EvidenceError):
                self.store.put('audit', [])
        self.assertFalse(self.path(ref).exists())
        self.assertEqual(list(self.objects.iterdir()), [])

    def test_link_failure_preserves_absence_and_cleans_temporary(self):
        ref, _ = evidence.encode('audit', [])
        with patch.object(evidence.os, 'link', side_effect=OSError('falha sintética')):
            with self.assertRaises(evidence.EvidenceError):
                self.store.put('audit', [])
        self.assertFalse(self.path(ref).exists())
        self.assertEqual(list(self.objects.iterdir()), [])

    def test_directory_fsync_failure_leaves_complete_orphan_not_success(self):
        ref, stored = evidence.encode('audit', [])
        real_fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('falha sintética após publicação')
            return real_fsync(fd)
        with patch.object(evidence.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(evidence.EvidenceError):
                self.store.put('audit', [])
        self.assertEqual(self.path(ref).read_bytes(), stored)
        self.assertEqual(self.store.read(ref)['payload'], [])
        self.assertEqual(list(self.objects.iterdir()), [self.path(ref)])

    def test_walk_dfs_unique_hashes_and_literal_free_payload(self):
        refs = self.graph()
        walked = list(self.store.walk(refs['review']))
        hashes = [ref['stored_sha256'] for ref, _ in walked]
        self.assertEqual(len(hashes), len(set(hashes)))
        self.assertEqual(set(hashes), {ref['stored_sha256'] for ref in refs.values()})
        self.assertEqual(walked[0][0], refs['review'])
        self.assertEqual(next(env['payload'] for ref, env in walked if ref == refs['report']), {'status': 'FAIL'})
        # Campos livres parecidos com caminhos/referências permanecem só dados.
        leaf = self.store.put('audit', [{'stored_sha256': 'a' * 64, 'path': '/inexistente'}])
        self.assertEqual(len(list(self.store.walk(leaf))), 1)

    def test_walk_missing_child_and_inconsistent_repeated_reference_refused(self):
        refs = self.graph()
        self.path(refs['audit']).unlink()
        with self.assertRaises(evidence.EvidenceError):
            list(self.store.walk(refs['review']))
        refs = self.graph()
        wrong = dict(refs['report'], json_bytes=refs['report']['json_bytes'] + 1)
        before = {path.name for path in self.objects.iterdir()}
        with self.assertRaises(evidence.EvidenceError):
            self.store.put('regression', {}, {'a': refs['report'], 'b': wrong})
        self.assertEqual({path.name for path in self.objects.iterdir()}, before)

    def test_walk_count_and_aggregate_budgets_count_each_hash_once(self):
        refs = self.graph()
        total_stored = sum(ref['stored_bytes'] for ref in refs.values())
        total_json = sum(ref['json_bytes'] for ref in refs.values())
        limits = [('MAX_OBJECTS', len(refs)), ('MAX_TOTAL_STORED_BYTES', total_stored),
                  ('MAX_TOTAL_JSON_BYTES', total_json)]
        for constant, exact in limits:
            with self.subTest(constant=constant):
                with patch.object(evidence, constant, exact):
                    self.assertEqual(len(list(self.store.walk(refs['review']))), len(refs))
                with patch.object(evidence, constant, exact - 1):
                    with self.assertRaises(evidence.EvidenceError):
                        list(self.store.walk(refs['review']))

    def test_walk_depth_and_cycle_are_refused(self):
        refs = self.graph()
        with patch.object(evidence, 'MAX_DEPTH', 1):
            with self.assertRaises(evidence.EvidenceError):
                list(self.store.walk(refs['review']))
        # Um ciclo real exigiria ponto fixo/colisão de hash; isola o algoritmo com read sintético.
        first = dict(refs['report'], stored_sha256='a' * 64)
        second = dict(refs['regression'], stored_sha256='b' * 64)
        envelopes = {'a' * 64: dict(schema=1, kind='report', payload={},
                                  links=dict(audit=refs['audit'], transcript=refs['transcript'], regression=second)),
                     'b' * 64: dict(schema=1, kind='regression', payload={}, links={'again': first})}
        real_read = self.store.read
        def synthetic(ref):
            return envelopes[ref['stored_sha256']] if ref['stored_sha256'] in envelopes else real_read(ref)
        with patch.object(self.store, 'read', side_effect=synthetic):
            with self.assertRaises(evidence.EvidenceError):
                list(self.store.walk(first))


    def test_put_validates_child_closure_before_publishing_root(self):
        absent_audit = evidence.encode('audit', [])[0]
        transcript = self.store.put('transcript', 'literal')
        links = dict(audit=absent_audit, transcript=transcript)
        parent, _ = evidence.encode('report', {}, links)
        before = {path.name for path in self.objects.iterdir()}
        with self.assertRaises(evidence.EvidenceError):
            self.store.put('report', {}, links)
        self.assertFalse(self.path(parent).exists())
        self.assertEqual({path.name for path in self.objects.iterdir()}, before)

    def test_validate_returns_terminal_closure_counts(self):
        refs = self.graph()
        self.assertEqual(self.store.validate(refs['review']),
                         dict(objects=len(refs),
                              stored_bytes=sum(ref['stored_bytes'] for ref in refs.values()),
                              json_bytes=sum(ref['json_bytes'] for ref in refs.values())))
        self.path(refs['audit']).unlink()
        with self.assertRaises(evidence.EvidenceError):
            self.store.validate(refs['review'])

    def test_read_consumes_verified_descriptor_without_reopening_path(self):
        ref = self.store.put('transcript', 'original')
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('reabertura por caminho')):
            self.assertEqual(self.store.read(ref)['payload'], 'original')

    def test_shared_descendants_count_toward_depth_after_shallower_visit(self):
        audit = self.store.put('audit', [])
        transcript = self.store.put('transcript', 'literal')
        leaves = dict(audit=audit, transcript=transcript)
        shared = self.store.put('report', {'shared': True}, leaves)
        nested = self.store.put('regression', {}, {'shared': shared})
        deeper = self.store.put('report', {'deeper': True}, dict(leaves, regression=nested))
        root = self.store.put('regression', {}, {'a_shared': shared, 'z_deeper': deeper})
        # a_shared: profundidades 1→2. z_deeper→regression→shared: 1→2→3→4.
        # O segundo encontro de shared precisa considerar seus descendentes já validados.
        real_read = self.store.read
        with patch.object(evidence, 'MAX_DEPTH', 3), patch.object(self.store, 'read', wraps=real_read) as reads:
            with self.assertRaises(evidence.EvidenceError):
                list(self.store.walk(root))
            self.assertEqual(sum(call.args[0] == shared for call in reads.call_args_list), 1)
        with patch.object(evidence, 'MAX_DEPTH', 4):
            self.assertEqual(len(list(self.store.walk(root))), 6)
            self.assertEqual(self.store.validate(root)['objects'], 6)

    def test_regression_review_roundtrip_preserves_payload_and_exact_links(self):
        refs = self.graph()
        payload = {'status': 'INFRA_ERROR', 'contagens': {'pass': 0, 'fail': 1},
                   'literal': ' saída\n\tsem truncar ', 'outcomes': [False, None, 1.0]}
        regression_links = {'v1': refs['report'], 'outro_2': refs['report']}
        regression = self.store.put('regression', payload, regression_links)
        self.assertEqual(self.store.read(regression),
                         dict(schema=1, kind='regression', payload=payload, links=regression_links))
        review_payload = {'status': 'PENDING', 'achados': [' motivo literal '], 'approved': False}
        review_links = dict(baseline=refs['report'], regression=regression)
        review = self.store.put('review', review_payload, review_links)
        self.assertEqual(self.store.read(review),
                         dict(schema=1, kind='review', payload=review_payload, links=review_links))
        self.assertEqual(self.store.validate(review)['objects'], 5)


    def test_process_kill_before_and_after_link_preserves_atomic_final_object(self):
        # Processo POSIX real; não simula queda de energia nem altera o produto.
        context = multiprocessing.get_context('fork')
        ref, stored = evidence.encode('transcript', 'publicação interrompida')
        for checkpoint in ('BEFORE_LINK', 'AFTER_LINK'):
            with self.subTest(checkpoint=checkpoint):
                directory = self.root / checkpoint.lower()
                directory.mkdir()
                parent, child = context.Pipe(duplex=True)
                real_link = os.link
                def publish():
                    parent.close()
                    def intercepted_link(*args, **kwargs):
                        if checkpoint == 'BEFORE_LINK':
                            child.send(checkpoint)
                            child.recv()  # O pai mata este processo sem liberar a espera.
                        result = real_link(*args, **kwargs)
                        if checkpoint == 'AFTER_LINK':
                            child.send(checkpoint)
                            child.recv()
                        return result
                    try:
                        with patch.object(evidence.os, 'link', side_effect=intercepted_link):
                            evidence.EvidenceStore(directory).put('transcript', 'publicação interrompida')
                        child.send('UNEXPECTED_SUCCESS')
                    except BaseException as error:
                        child.send(('UNEXPECTED_ERROR', type(error).__name__, str(error)))
                    finally:
                        child.close()
                process = context.Process(target=publish)
                try:
                    process.start()
                    child.close()
                    self.assertTrue(parent.poll(5), 'Processo não alcançou o checkpoint no prazo')
                    self.assertEqual(parent.recv(), checkpoint)
                    self.assertTrue(process.is_alive())
                    process.terminate()
                    process.join(3)
                    self.assertFalse(process.is_alive(), 'Processo não encerrou após terminate')
                    self.assertLess(process.exitcode, 0)
                    final = directory / (ref['stored_sha256'] + '.json.gz')
                    if checkpoint == 'BEFORE_LINK':
                        self.assertFalse(final.exists())
                        with self.assertRaises(evidence.EvidenceError):
                            evidence.EvidenceStore(directory).read(ref)
                    else:
                        self.assertEqual(final.read_bytes(), stored)
                        self.assertEqual(evidence.EvidenceStore(directory).read(ref)['payload'],
                                         'publicação interrompida')
                    pending = list(directory.glob('.pending-*'))
                    self.assertEqual(len(pending), 1)
                    self.assertEqual(pending[0].read_bytes(), stored)
                    if parent.poll():
                        # poll também sinaliza EOF após a morte, sem mensagem de sucesso.
                        with self.assertRaises(EOFError):
                            parent.recv()
                finally:
                    if process.pid is not None and process.is_alive():
                        process.terminate()
                        process.join(3)
                        if process.is_alive():
                            process.kill()
                            process.join(3)
                    parent.close()
                    child.close()
                    if process.pid is not None and not process.is_alive():
                        process.close()


class EvidenceRunTests(unittest.TestCase):
    """Coletas sintéticas novas; nenhum entrypoint histórico ou runtime é executado."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='evidence-runs-test-', dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence_root = self.root / 'evidence'
        self.setting = patch.object(evidence, 'EVIDENCE_ROOT', self.evidence_root)
        self.setting.start()
        self.addCleanup(self.setting.stop)

    def flat(self):
        return dict(result='FAIL', mode='synthetic', candidate_ref='candidate literal',
                    at_version='a' * 64, tests_run=3, limits=[' processo sintético '],
                    audit=[{'returncode': 1, 'stdout': ' saída completa ', 'stderr': 'erro\n',
                            'command': ['/caminho/histórico', '--literal'],
                            'inspect': {'Mounts': [{'Source': '/não/abrir'}]},
                            'proof': {'marker_received': True}}],
                    transcript=' transcrição\n\tsem truncamento ',
                    outcomes=[{'status': 'FAIL'}, {'status': 'INFRA_ERROR'}, {'status': 'SKIP'}])

    def object_names(self):
        directory = self.evidence_root / 'objects/sha256'
        return {path.name for path in directory.iterdir()} if directory.exists() else set()

    def reconstruct(self, store, ref):
        envelope = store.read(ref)
        payload = dict(envelope['payload'])
        layout = payload.pop('collection_layout')
        audit = store.read(envelope['links']['audit'])['payload']
        transcript = store.read(envelope['links']['transcript'])['payload']
        if layout == 'flat':
            payload.update(audit=audit, transcript=transcript)
        elif layout == 'integration':
            payload['baseline'] = dict(payload['baseline'], audit=audit, text_report=transcript)
        elif layout == 'boundary':
            payload['runs'] = {entry['label']: entry['run'] for entry in audit}
        else:
            self.fail('Layout inesperado')
        return payload, envelope

    def test_all_collection_layouts_preserve_complete_capture_without_inline_audit(self):
        flat = self.flat()
        integration = dict(result='FAIL', limits=['integração sintética'],
                           baseline=dict(status='FAIL', audit=flat['audit'], text_report=flat['transcript'],
                                         methods={'case': {'outcome': 'INFRA_ERROR'}}, extra=[' literal ']),
                           control_hash='b' * 64)
        boundary = dict(result='FAIL', limits=['fronteira sintética'],
                        runs={'z_case': flat['audit'][0], 'a_case': {'outcome': 'TIMEOUT', 'stderr': 'inteiro'}},
                        text='metadado preservado')
        store = evidence.default_store()
        for layout, report in (('flat', flat), ('integration', integration), ('boundary', boundary)):
            with self.subTest(layout=layout):
                ref = evidence.store_report(store, report)
                reconstructed, envelope = self.reconstruct(store, ref)
                self.assertEqual(reconstructed, report)
                self.assertEqual(envelope['payload']['collection_layout'], layout)
                self.assertEqual(set(envelope['links']), {'audit', 'transcript'})
                self.assertNotIn('audit', envelope['payload'])
                self.assertNotIn('transcript', envelope['payload'])
                self.assertNotIn('runs', envelope['payload'])
                if layout == 'integration':
                    self.assertNotIn('audit', envelope['payload']['baseline'])
                    self.assertNotIn('text_report', envelope['payload']['baseline'])
                self.assertIn('stdout', canonical(store.read(envelope['links']['audit'])).decode())

    def test_report_regression_link_keeps_complete_external_reports_separate(self):
        store = evidence.default_store()
        historical = evidence.store_report(store, self.flat())
        regression = store.put('regression', {'result': 'FAIL'}, {'v1': historical})
        report = dict(self.flat(), result='INFRA_ERROR')
        ref = evidence.store_report(store, report, regression=regression)
        reconstructed, envelope = self.reconstruct(store, ref)
        self.assertEqual(reconstructed, report)
        self.assertEqual(envelope['links']['regression'], regression)
        self.assertNotIn('regression', envelope['payload'])
        self.assertEqual(store.read(regression)['links'], {'v1': historical})
        self.assertEqual(store.validate(ref)['objects'], 5)

    def test_save_new_runs_deduplicate_objects_and_preserve_historical_sentinels(self):
        self.evidence_root.mkdir()
        sentinels = {name: ('histórico literal ' + name).encode()
                     for name in ('integration.json', 'boundary.json', 'block-instrumentation.json',
                                  'unblock-baseline.json')}
        for name, raw in sentinels.items():
            (self.evidence_root / name).write_bytes(raw)
        first, summary = evidence.save_run(self.flat())
        names = self.object_names()
        second, repeated = evidence.save_run(self.flat())
        self.assertNotEqual(first, second)
        self.assertEqual(summary['report'], repeated['report'])
        self.assertEqual(self.object_names(), names)
        self.assertEqual(len(names), 3)
        from uuid import UUID
        for path, metadata in ((first, summary), (second, repeated)):
            self.assertEqual(path.parent.parent, self.evidence_root / 'runs')
            self.assertEqual(str(UUID(path.parent.name)), path.parent.name)
            self.assertEqual(path.name, 'summary.json')
            self.assertEqual(metadata['run_id'], path.parent.name)
            store, ref, payload = evidence.read_run(path)
            self.assertEqual(ref, metadata['report'])
            self.assertEqual(payload['result'], 'FAIL')
            self.assertEqual(self.reconstruct(store, ref)[0], self.flat())
            self.assertNotIn('audit', metadata)
            self.assertNotIn('transcript', metadata)
        for name, raw in sentinels.items():
            self.assertEqual((self.evidence_root / name).read_bytes(), raw)

    def test_validate_output_checks_path_without_any_write(self):
        first = evidence.validate_output(None)
        second = evidence.validate_output(None)
        self.assertNotEqual(first, second)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(evidence.validate_output(first), first)
        self.assertEqual(list(self.root.iterdir()), [])
        bad = [Path('summary.json'), self.root / 'summary.json',
               self.evidence_root / 'integration.json',
               self.evidence_root / 'runs/not-uuid/summary.json',
               self.evidence_root / 'runs/AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA/summary.json',
               first.parent / 'other.json', first.parent / '../summary.json']
        for output in bad:
            with self.subTest(output=str(output)), self.assertRaises(evidence.EvidenceError):
                evidence.validate_output(output)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_explicit_output_is_immutable_and_existing_destination_fails_before_collection(self):
        output = evidence.validate_output(None)
        path, summary = evidence.save_run(self.flat(), output=output)
        self.assertEqual(path, output)
        before = path.read_bytes()
        objects = self.object_names()
        for call in (lambda: evidence.validate_output(path),
                     lambda: evidence.save_run(dict(self.flat(), result='PASS'), output=path)):
            with self.assertRaises(evidence.EvidenceError):
                call()
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(self.object_names(), objects)
        self.assertEqual(json.loads(before), summary)

    def test_symlink_ancestor_and_summary_are_refused(self):
        self.evidence_root.mkdir()
        actual = self.root / 'actual-runs'
        actual.mkdir()
        (self.evidence_root / 'runs').symlink_to(actual, target_is_directory=True)
        target = self.evidence_root / 'runs/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/summary.json'
        with self.assertRaises(evidence.EvidenceError):
            evidence.validate_output(target)
        with self.assertRaises(evidence.EvidenceError):
            evidence.save_run(self.flat(), output=target)
        self.assertEqual(list(actual.iterdir()), [])
        (self.evidence_root / 'runs').unlink()
        path, _ = evidence.save_run(self.flat())
        saved = self.root / 'saved-summary.json'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_run(path)

    def test_summary_size_budget_refuses_publication_without_truncating_capture(self):
        report = dict(self.flat(), limits=['x' * (32 * 1024)])
        output = evidence.validate_output(None)
        with self.assertRaises(evidence.EvidenceError):
            evidence.save_run(report, output=output)
        self.assertFalse(output.exists())
        # Objetos órfãos completos são permitidos; não há resumo que alegue sucesso.
        store = evidence.default_store()
        report_ref = evidence.store_report(store, report)
        self.assertEqual(self.reconstruct(store, report_ref)[0], report)
        self.assertEqual(store.validate(report_ref)['objects'], 3)

    def test_read_summary_rejects_forged_pass_unknown_fields_and_wrong_types(self):
        path, summary = evidence.save_run(self.flat())
        original = path.read_bytes()
        for change in ({'result': 'PASS'}, {'schema': True}, {'schema': 1.0},
                       {'run_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'},
                       {'tests_run': True}, {'extra': 'não permitido'}):
            with self.subTest(change=change):
                forged = dict(summary, **change)
                path.write_bytes(canonical(forged) + b'\n')
                with self.assertRaises(evidence.EvidenceError):
                    evidence.read_run(path)
        path.write_bytes(original)
        self.assertEqual(evidence.read_run(path)[1], summary['report'])

    def test_read_summary_requires_complete_closure_and_valid_uuid(self):
        path, summary = evidence.save_run(self.flat())
        store = evidence.default_store()
        audit = store.read(summary['report'])['links']['audit']
        (store.root / (audit['stored_sha256'] + '.json.gz')).unlink()
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_run(path)
        invalid = self.evidence_root / 'runs/not-uuid/summary.json'
        invalid.parent.mkdir()
        invalid.write_bytes(path.read_bytes())
        with self.assertRaises(evidence.EvidenceError):
            evidence.read_run(invalid)

    def test_mixed_collection_layouts_are_refused_instead_of_losing_capture(self):
        baseline = dict(audit=[{'baseline': 'não perder'}], text_report='baseline completo')
        cases = [dict(self.flat(), baseline=baseline),
                 dict(self.flat(), runs={'boundary': {'stdout': 'não perder'}}),
                 dict(result='FAIL', baseline=baseline, runs={'boundary': {'stdout': 'não perder'}}),
                 dict(result='FAIL', baseline=baseline, transcript='top-level não perder')]
        store = evidence.default_store()
        for report in cases:
            with self.subTest(keys=sorted(report)), self.assertRaises(evidence.EvidenceError):
                evidence.store_report(store, report)


class ReferencedEvidenceGateTests(unittest.TestCase):
    """Gate pré-publicação com fixtures locais; não reexecuta coletas históricas."""

    @classmethod
    def setUpClass(cls):
        import block_seal
        cls.seals = block_seal
        cls.project = BASE.parents[1]
        cls.temp = tempfile.TemporaryDirectory(prefix='block-integration-gate-test-', dir=BASE)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.current_manifest = block_seal.create_seal(cls.project, cls.work / 'seal7', generation=7)
        cls.body = json.loads(json.dumps(cls.current_manifest['body']))
        cls.coverage = (BASE / 'seals/v6/tests/coverage.json').read_bytes()
        objects = cls.work / 'objects'
        objects.mkdir()
        cls.store = evidence.EvidenceStore(objects)
        cls.external_data = {}
        cls.external_refs = {}
        for name in ('v1', 'v2', 'v5'):
            data = json.loads((BASE / f'evidence/unblock-regression-{name}.json').read_bytes())
            cls.external_data[name] = data
            cls.external_refs[name] = evidence.store_report(cls.store, data)
        complementary = dict(result='PASS', tests_run=39, errors=[], failures=[], skipped=[],
                             candidate_ref=block_seal.UNBLOCK_BASELINE_COMMIT,
                             transcript='Complementar sintético, não é coleta de execução real.')
        cls.external_refs['complementary'] = evidence.store_report(cls.store, complementary)
        # Coleta I/T nova já produzida pelo controlador; leitura histórica apenas.
        source, source_ref, source_payload = evidence.read_run(BASE / 'evidence/runs/'
            '0c35dbbf-bc96-490b-99cc-d4029a081c32/summary.json')
        # Contexto sintético do gate: a review/coleta histórica continua imutável.
        # Os14 hashes medidos pertencem àquela coleta, não às fontes vivas futuras.
        # Isto não afirma que há um snapshot publicado para este corpo sintético.
        reviewed = source_payload['infrastructure_review']['files']
        inverse_sources = {source_name: target for target, source_name
                           in block_seal.EVIDENCE_SOURCE_FILES.items()}
        assert len(reviewed) == 14
        for source_name, measured_hash in reviewed.items():
            cls.body['files'][inverse_sources[source_name]] = measured_hash
        cls.manifest = dict(body=cls.body,
                            at_version='sha256:' + hashlib.sha256(canonical(cls.body)).hexdigest())
        copied = {}
        def copy_closure(ref):
            key = ref['stored_sha256']
            if key in copied:
                return copied[key]
            envelope = source.read(ref)
            links = {name: copy_closure(child) for name, child in envelope['links'].items()}
            copied[key] = cls.store.put(envelope['kind'], envelope['payload'], links)
            return copied[key]
        cls.external_refs['i1_i7_t1_t8'] = copy_closure(source_ref)
        cls.regression_payload = dict(candidate_ref=block_seal.UNBLOCK_BASELINE_COMMIT, result='PASS',
                                     results={name: 'PASS' for name in cls.external_refs})
        cls.regression = cls.store.put('regression', cls.regression_payload, cls.external_refs)
        cls.baseline = json.loads((BASE / 'evidence/unblock-baseline.json').read_bytes())
        cls.baseline.pop('external_evidence', None)
        cls.baseline.pop('external_evidence_sha256', None)
        cls.baseline.update(at_version=cls.manifest['at_version'], execution_at_version=block_seal.V6_PIN,
                            source_files=cls.body['files'], specs=cls.body['specs'],
                            external_checks={'R1': 'PASS'})
        cls.report = evidence.store_report(cls.store, cls.baseline, regression=cls.regression)

    def gate(self, ref=None, coverage=None):
        return self.seals.validate_referenced_baseline(self.body, self.store,
                    self.report if ref is None else ref,
                    coverage_bytes=self.coverage if coverage is None else coverage)

    def cloned(self, value):
        return json.loads(json.dumps(value))

    def test_positive_closure_is_complete_but_does_not_publish(self):
        self.assertEqual(self.gate(), dict(status='PASS', tests_run=16, infrastructure_errors=0))
        self.assertEqual(self.manifest['body']['status'], 'DRAFT')
        self.assertIsNone(self.manifest['body']['review'])
        self.assertEqual(self.store.read(self.report)['links']['regression'], self.regression)
        self.assertEqual(self.store.read(self.regression)['links'], self.external_refs)

    def test_identity_outcomes_and_boolean_counts_cannot_be_replaced_by_pass(self):
        changes = [dict(result='INCOMPLETE'), dict(tests_run=True), dict(candidate_ref='outro'),
                   dict(candidate_hashes={}), dict(at_version=self.seals.V6_PIN),
                   dict(execution_at_version=self.manifest['at_version']), dict(source_files={}),
                   dict(specs={}), dict(outcomes={}), dict(failed_methods=[]),
                   dict(external_checks={'R1': 'NOT_RUN'})]
        for change in changes:
            with self.subTest(fields=list(change)):
                bad = evidence.store_report(self.store, dict(self.baseline, **change), regression=self.regression)
                with self.assertRaises(self.seals.SealError):
                    self.gate(bad)

        # Identidades internas coerentes não substituem a medição histórica da review.
        changed_body = self.cloned(self.body)
        changed_body['files']['controller/evidence_store.py'] = '0' * 64
        changed = dict(self.baseline, source_files=changed_body['files'],
                       at_version='sha256:' + hashlib.sha256(canonical(changed_body)).hexdigest())
        ref = evidence.store_report(self.store, changed, regression=self.regression)
        with self.assertRaises(self.seals.SealError):
            self.seals.validate_referenced_baseline(changed_body, self.store, ref,
                                                   coverage_bytes=self.coverage)

    def test_coverage_bytes_and_missing_regression_are_refused(self):
        with self.assertRaises(self.seals.SealError):
            self.gate(coverage=self.coverage + b' ')
        bad = evidence.store_report(self.store, self.baseline)
        with self.assertRaises(self.seals.SealError):
            self.gate(bad)
        forged = self.cloned(json.loads(self.coverage))
        forged['methods'].pop(next(iter(forged['methods'])))
        bad = evidence.store_report(self.store, dict(self.baseline, coverage=forged), regression=self.regression)
        with self.assertRaises(self.seals.SealError):
            self.gate(bad)

    def test_baseline_audit_infrastructure_and_incomplete_cleanup_are_refused(self):
        for audit in ([dict(classification='FUNCTIONAL_EXIT', outcome='EXIT', returncode=125, removed=True)],
                      [dict(classification='FUNCTIONAL_EXIT', outcome='EXIT', returncode=124, removed=True)],
                      [dict(classification='FUNCTIONAL_EXIT', outcome='EXIT', returncode=True, removed=True)],
                      [dict(classification='INFRA_ERROR', outcome='EXIT', returncode=0, removed=True)],
                      [dict(classification='FUNCTIONAL_EXIT', outcome='EXIT', returncode=0, removed=False)], []):
            with self.subTest(audit=audit):
                bad = evidence.store_report(self.store, dict(self.baseline, audit=audit), regression=self.regression)
                with self.assertRaises(self.seals.SealError):
                    self.gate(bad)

    def test_external_pass_cannot_hide_changed_identity_results_or_infrastructure(self):
        cases = [('v1', {'result': 'FAIL'}), ('v1', {'tests_run': True}),
                 ('v1', {'candidate_hashes': {}}), ('v2', {'at_version': self.seals.V5_PIN}),
                 ('v5', {'outcomes': {'Fictitious.test_fake': 'PASS'}}),
                 ('v5', {'temporary_removed': False}), ('v2', {'candidate_ref': 'outro'}),
                 ('v1', {'audit': [dict(classification='FUNCTIONAL_EXIT', outcome='EXIT', returncode=125, removed=True)]}),
                 ('v2', {'audit': [dict(classification='INFRA_ERROR', outcome='EXIT', returncode=0, removed=True)]})]
        for name, change in cases:
            with self.subTest(name=name, fields=list(change)):
                replaced = evidence.store_report(self.store, dict(self.external_data[name], **change))
                regression = self.store.put('regression', self.regression_payload,
                                             dict(self.external_refs, **{name: replaced}))
                report = evidence.store_report(self.store, self.baseline, regression=regression)
                with self.assertRaises(self.seals.SealError):
                    self.gate(report)

    def test_default_seal_uses_frozen_six_and_seven_publication_remains_blocked(self):
        frozen = json.loads((BASE / 'seals/v6/manifest.json').read_bytes())
        default = self.seals.create_seal(self.project, self.work / 'seal-default6')
        self.assertEqual(default['body'], dict(frozen['body'], status='DRAFT', review=None))
        self.assertEqual(default['body']['generation'], 6)
        self.assertEqual(default['body']['files'], frozen['body']['files'])
        current = self.current_manifest['body']
        self.assertEqual(current['generation'], 7)
        self.assertEqual(current['status'], 'DRAFT')
        self.assertEqual(current['previous_at_version'], self.seals.V6_PIN)
        self.assertEqual(self.current_manifest['at_version'],
                         'sha256:' + hashlib.sha256(canonical(current)).hexdigest())
        self.assertEqual(self.seals.verify_seal(self.work / 'seal7', self.current_manifest['at_version']),
                         self.current_manifest)
        self.assertEqual(self.body['files']['tests/coverage.json'],
                         frozen['body']['files']['tests/coverage.json'])
        self.assertEqual(self.body['files']['tests/test_unblock.py'],
                         frozen['body']['files']['tests/test_unblock.py'])
        blocked = self.work / 'publish-forbidden'
        with self.assertRaises(self.seals.SealError):
            self.seals.create_seal(self.project, blocked, review={'status': 'PASS'}, generation=7)
        self.assertFalse(blocked.exists())

    def test_old_baseline_cli_and_invalid_output_stop_before_any_collection(self):
        import integrate_block
        from contextlib import redirect_stderr
        with patch.object(integrate_block, 'create_seal', side_effect=AssertionError('coleta proibida')) as create, \
             patch.object(integrate_block, 'run_suite', side_effect=AssertionError('runtime proibido')) as run, \
             patch.object(integrate_block.tempfile, 'TemporaryDirectory', side_effect=AssertionError('escrita proibida')) as temporary:
            output = io.StringIO()
            with redirect_stderr(output), self.assertRaises(SystemExit) as stopped:
                integrate_block.main(['--baseline'])
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn('--generation 7', output.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(integrate_block.main(['--baseline', '--generation', '7',
                    '--output', str(self.work / 'protected.json')]), 125)
            create.assert_not_called()
            run.assert_not_called()
            temporary.assert_not_called()

    def test_external_it_pass_flags_do_not_replace_collected_proofs(self):
        complete = self.store.read(self.external_refs['i1_i7_t1_t8'])
        boundary_group = self.store.read(complete['links']['regression'])
        boundary = self.store.read(boundary_group['links']['boundary'])
        empty_audit = self.store.put('audit', [])
        empty_boundary = self.store.put('report', boundary['payload'],
                                       dict(boundary['links'], audit=empty_audit))
        empty_group = self.store.put('regression', boundary_group['payload'], {'boundary': empty_boundary})
        missing_review = dict(complete['payload'])
        missing_review.pop('infrastructure_review', None)
        variants = [
            evidence.store_report(self.store, dict(result='PASS',
                criteria={f'I{i}': {'status': 'PASS'} for i in range(1, 8)}),
                regression=complete['links']['regression']),
            self.store.put('report', missing_review, complete['links']),
            self.store.put('report', complete['payload'], dict(complete['links'], regression=empty_group)),
        ]
        for variant in variants:
            with self.subTest(variant=variant['stored_sha256']):
                regression = self.store.put('regression', self.regression_payload,
                    dict(self.external_refs, i1_i7_t1_t8=variant))
                report = evidence.store_report(self.store, self.baseline, regression=regression)
                with self.assertRaises(self.seals.SealError):
                    self.gate(report)
