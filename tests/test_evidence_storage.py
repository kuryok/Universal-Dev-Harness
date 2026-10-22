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
