"""Codec e publicação de evidências do bootstrap; não decide aceite do produto."""
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid
import zlib

PROJECT_ROOT = Path(__file__).absolute().parents[2]
MAX_STORED_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TOTAL_STORED_BYTES = 16 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 64 * 1024 * 1024
MAX_OBJECTS = 256
MAX_DEPTH = 8
MAX_JSON_DEPTH = 64
KINDS = ('audit', 'transcript', 'report', 'regression', 'review')
REFERENCE_FIELDS = {'schema', 'kind', 'codec', 'stored_sha256', 'stored_bytes', 'json_sha256', 'json_bytes'}


class EvidenceError(ValueError):
    """Falha de infraestrutura; nunca representa falha funcional esperada."""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _reference(value):
    if (type(value) is not dict or set(value) != REFERENCE_FIELDS
            or type(value['schema']) is not int or value['schema'] != 1
            or type(value['kind']) is not str or value['kind'] not in KINDS
            or type(value['codec']) is not str or value['codec'] != 'json-gzip-v1'):
        raise EvidenceError('Referência de evidência inválida.')
    for name in ('stored_sha256', 'json_sha256'):
        if type(value[name]) is not str or not re.fullmatch('[0-9a-f]{64}', value[name]):
            raise EvidenceError('Hash de evidência inválido.')
    for name, maximum in (('stored_bytes', MAX_STORED_BYTES), ('json_bytes', MAX_JSON_BYTES)):
        if type(value[name]) is not int or not 0 < value[name] <= maximum:
            raise EvidenceError('Tamanho de evidência excede o limite ou é inválido.')
    return dict(value)


def _json_value(value, depth=0):
    if depth > MAX_JSON_DEPTH:
        raise EvidenceError('JSON excede a profundidade permitida.')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise EvidenceError('Conteúdo não pertence ao formato JSON estrito.')


def _canonical(value):
    _json_value(value)
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for part in encoder.iterencode(value):
        chunk = part.encode('utf-8')
        if len(result) + len(chunk) > MAX_JSON_BYTES:
            raise EvidenceError('JSON excede o limite de bytes.')
        result.extend(chunk)
    return bytes(result)


def _envelope(value):
    if (type(value) is not dict or set(value) != {'schema', 'kind', 'payload', 'links'}
            or type(value['schema']) is not int or value['schema'] != 1
            or type(value['kind']) is not str or value['kind'] not in KINDS
            or type(value['links']) is not dict):
        raise EvidenceError('Envelope de evidência inválido.')
    kind, payload, links = value['kind'], value['payload'], value['links']
    if kind == 'audit':
        valid = type(payload) is list and all(type(item) is dict for item in payload) and not links
    elif kind == 'transcript':
        valid = type(payload) is str and not links
    else:
        valid = type(payload) is dict
    if not valid:
        raise EvidenceError('Payload ou vínculos incompatíveis com o tipo de evidência.')
    expected = {'audit': {}, 'transcript': {}, 'report': {'audit': 'audit', 'transcript': 'transcript'},
                'review': {'baseline': 'report', 'regression': 'regression'}}
    if kind == 'regression':
        if not links or any(type(name) is not str or not re.fullmatch('[a-z][a-z0-9_]{0,63}', name) for name in links):
            raise EvidenceError('Vínculos de regressão inválidos.')
        required = {name: 'report' for name in links}
    else:
        required = expected[kind]
        if kind == 'report' and 'regression' in links:
            required = dict(required, regression='regression')
    if set(links) != set(required):
        raise EvidenceError('Conjunto de vínculos incompatível com o tipo de evidência.')
    for name, target_kind in required.items():
        if _reference(links[name])['kind'] != target_kind:
            raise EvidenceError('Tipo do vínculo de evidência incompatível.')
    return value


def encode(kind, payload, links=None):
    """Produz bytes e referência, sem I/O nem decisão sobre o conteúdo do relatório."""
    try:
        envelope = _envelope(dict(schema=1, kind=kind, payload=payload, links={} if links is None else links))
        raw = _canonical(envelope)
        output = io.BytesIO()
        with gzip.GzipFile(fileobj=output, mode='wb', filename='', compresslevel=9, mtime=0) as stream:
            stream.write(raw)
        data = output.getvalue()
        reference = dict(schema=1, kind=kind, codec='json-gzip-v1', stored_sha256=_hash(data),
                         stored_bytes=len(data), json_sha256=_hash(raw), json_bytes=len(raw))
        return _reference(reference), data
    except (ValueError, TypeError, UnicodeError, RecursionError, MemoryError, OverflowError) as error:
        raise EvidenceError('Não foi possível codificar a evidência: ' + str(error)) from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise EvidenceError('JSON contém chave duplicada.')
        result[key] = value
    return result


def _constant(value):
    raise EvidenceError('JSON contém número não finito.')


def decode(reference, data):
    """Confere hashes, tamanho real e um único membro gzip antes de consumir JSON."""
    try:
        reference = _reference(reference)
        if type(data) is not bytes or len(data) != reference['stored_bytes'] or _hash(data) != reference['stored_sha256']:
            raise EvidenceError('Bytes comprimidos divergem da referência.')
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(data, reference['json_bytes'] + 1)
        if (len(raw) != reference['json_bytes'] or not decoder.eof
                or decoder.unused_data or decoder.unconsumed_tail or _hash(raw) != reference['json_sha256']):
            raise EvidenceError('Gzip incompleto, residual ou divergente da referência.')
        value = _envelope(json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant))
        if value['kind'] != reference['kind'] or _canonical(value) != raw:
            raise EvidenceError('JSON não canônico ou de tipo divergente.')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, MemoryError, OverflowError, zlib.error) as error:
        raise EvidenceError('Não foi possível ler a evidência: ' + str(error)) from None


@contextmanager
def _directory(path):
    """Percorre ancestrais sem seguir symlinks e mantém o diretório aberto."""
    descriptor = None
    try:
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_at(directory, reference):
    name = reference['stored_sha256'] + '.json.gz'
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != reference['stored_bytes']:
            raise EvidenceError('Objeto não regular ou de tamanho divergente.')
        data = bytearray()
        while len(data) <= reference['stored_bytes']:
            chunk = os.read(descriptor, min(65536, reference['stored_bytes'] + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        result = bytes(data)
        return result
    finally:
        os.close(descriptor)


class EvidenceStore:
    """Armazena objetos em diretório preexistente e confiável dentro do projeto."""

    def __init__(self, root):
        self.root = Path(root)
        if (not self.root.is_absolute() or '..' in self.root.parts
                or self.root == PROJECT_ROOT or not self.root.is_relative_to(PROJECT_ROOT)):
            raise EvidenceError('Raiz de evidências fora do diretório permitido do projeto.')
        try:
            with _directory(self.root):
                pass
        except OSError:
            raise EvidenceError('Raiz de evidências ausente ou redirecionada.') from None

    def read(self, reference):
        try:
            reference = _reference(reference)
            with _directory(self.root) as directory:
                data = _read_at(directory, reference)
            return decode(reference, data)
        except OSError:
            raise EvidenceError('Não foi possível abrir o objeto regular de evidência.') from None

    def _walk(self, reference, proposed=None):
        seen, active, heights = {}, set(), {}
        totals = [0, 0]

        def visit(item, depth):
            item = _reference(item)
            key = item['stored_sha256']
            if depth > MAX_DEPTH or key in active:
                raise EvidenceError('Fechamento excede a profundidade ou contém ciclo.')
            if key in seen:
                if seen[key] != item:
                    raise EvidenceError('Referências repetidas inconsistentes.')
                if depth + heights[key] > MAX_DEPTH:
                    raise EvidenceError('Fechamento excede a profundidade permitida.')
                return
            totals[0] += item['stored_bytes']
            totals[1] += item['json_bytes']
            if (len(seen) >= MAX_OBJECTS or totals[0] > MAX_TOTAL_STORED_BYTES
                    or totals[1] > MAX_TOTAL_JSON_BYTES):
                raise EvidenceError('Fechamento excede o orçamento agregado de evidências.')
            seen[key] = item
            active.add(key)
            envelope = proposed if depth == 0 and proposed is not None else self.read(item)
            children = tuple(envelope['links'][name] for name in sorted(envelope['links']))
            yield item, envelope
            del envelope
            for child in children:
                yield from visit(child, depth + 1)
            heights[key] = max((1 + heights[child['stored_sha256']] for child in children), default=0)
            active.remove(key)

        yield from visit(reference, 0)

    def walk(self, reference):
        """Itera sem cache de payloads; o fechamento só é provado ao esgotar o iterador."""
        yield from self._walk(reference)

    def validate(self, reference):
        """Retorna contadores somente depois de validar todo o fechamento."""
        result = dict(objects=0, stored_bytes=0, json_bytes=0)
        for item, envelope in self.walk(reference):
            result['objects'] += 1
            result['stored_bytes'] += item['stored_bytes']
            result['json_bytes'] += item['json_bytes']
            del envelope
        return result

    def put(self, kind, payload, links=None):
        reference, data = encode(kind, payload, links)
        envelope = decode(reference, data)
        for _, item in self._walk(reference, proposed=envelope):
            del item
        try:
            with _directory(self.root) as directory:
                _publish_at(directory, reference['stored_sha256'] + '.json.gz', data, reference)
        except OSError:
            raise EvidenceError('Falha na publicação atômica da evidência.') from None
        return reference


EVIDENCE_ROOT = PROJECT_ROOT / 'bootstrap/slice1/evidence'
MAX_SUMMARY_BYTES = 32 * 1024


def _publish_at(directory, name, data, reference=None):
    pending = '.pending-' + uuid.uuid4().hex
    created = False
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
        created = True
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(pending, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            if reference is None or _read_at(directory, reference) != data:
                raise EvidenceError('Destino existente não pode ser sobrescrito.')
        os.fsync(directory)
    finally:
        if created:
            os.unlink(pending, dir_fd=directory)


def _mkdir_project(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(PROJECT_ROOT):
        raise EvidenceError('Diretório fora da raiz autorizada.')
    try:
        with _directory(PROJECT_ROOT) as base:
            current = os.dup(base)
            try:
                for part in path.relative_to(PROJECT_ROOT).parts:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=current)
                    os.close(current)
                    current = child
            finally:
                os.close(current)
    except OSError:
        raise EvidenceError('Diretório de coleta ausente ou redirecionado.') from None


def default_store():
    root = EVIDENCE_ROOT / 'objects/sha256'
    _mkdir_project(root)
    return EvidenceStore(root)


def validate_output(output):
    """Confere destino novo sem criar diretórios ou coletar dados."""
    path = EVIDENCE_ROOT / 'runs' / str(uuid.uuid4()) / 'summary.json' if output is None else Path(output)
    if not path.is_absolute() or '..' in path.parts or path.name != 'summary.json':
        raise EvidenceError('Destino deve ser um resumo novo sob evidence/runs/UUID/.')
    try:
        if path.parent.parent != EVIDENCE_ROOT / 'runs' or str(uuid.UUID(path.parent.name)) != path.parent.name:
            raise ValueError()
        for ancestor in [*reversed(path.parent.parents), path.parent, path]:
            if ancestor.is_symlink() or (ancestor.exists() and ancestor != path and not ancestor.is_dir()):
                raise ValueError()
        if path.exists():
            raise ValueError()
    except (ValueError, OSError):
        raise EvidenceError('Destino histórico, existente ou redirecionado não é permitido.') from None
    return path


def store_report(store, report, *, regression=None):
    """Separa os três layouts conhecidos; não interpreta campos livres como referências."""
    if type(report) is not dict or 'collection_layout' in report:
        raise EvidenceError('Relatório de coleta inválido.')
    _json_value(report)
    nested = type(report.get('baseline')) is dict and 'audit' in report['baseline']
    if ((nested or 'runs' in report) and ('audit' in report or 'transcript' in report)
            or nested and 'runs' in report):
        raise EvidenceError('Layouts de coleta mistos não são permitidos.')
    payload = dict(report)
    audit, transcript = payload.pop('audit', []), payload.pop('transcript', '')
    layout = 'flat'
    if 'baseline' in payload and type(payload['baseline']) is dict and 'audit' in payload['baseline']:
        baseline = dict(payload['baseline'])
        audit, transcript = baseline.pop('audit'), baseline.pop('text_report', '')
        payload['baseline'] = baseline
        layout = 'integration'
    elif 'runs' in payload:
        runs = payload.pop('runs')
        if type(runs) is not dict:
            raise EvidenceError('Execuções de fronteira inválidas.')
        audit = [dict(label=label, run=value) for label, value in runs.items()]
        layout = 'boundary'
    payload['collection_layout'] = layout
    links = dict(audit=store.put('audit', audit), transcript=store.put('transcript', transcript))
    if regression is not None:
        links['regression'] = regression
    return store.put('report', payload, links)


def _summary(payload, reference, run_id):
    baseline = payload.get('baseline')
    baseline = baseline if type(baseline) is dict else {}
    manifest = payload.get('manifest')
    manifest = manifest if type(manifest) is dict else {}
    body = manifest.get('body')
    body = body if type(body) is dict else {}
    counts = {}
    for key in ('failures', 'errors', 'skipped'):
        value = payload.get(key, baseline.get(key))
        counts[key] = len(value) if type(value) is list else value if type(value) is int else None
    return dict(schema=1, run_id=run_id, report=reference, result=payload.get('result'),
                mode=payload.get('mode'), candidate_ref=payload.get('candidate_ref', baseline.get('candidate_reference')),
                candidate_hashes=payload.get('candidate_hashes', baseline.get('candidate_hashes')),
                at_version=payload.get('at_version'), specs=payload.get('specs', body.get('specs')),
                tests_run=payload.get('tests_run', baseline.get('tests_run')), counts=counts,
                limits=payload.get('limits', payload.get('limitations', [])))


def save_run(report, output=None, *, regression=None):
    """Publica coleta nova; o resumo não concede aceite nem substitui o fechamento."""
    destination = validate_output(output)
    store = default_store()
    reference = store_report(store, report, regression=regression)
    store.validate(reference)
    summary = _summary(store.read(reference)['payload'], reference, destination.parent.name)
    raw = _canonical(summary) + b'\n'
    if len(raw) > MAX_SUMMARY_BYTES:
        raise EvidenceError('Resumo excede o limite de bytes.')
    _mkdir_project(destination.parent)
    try:
        with _directory(destination.parent) as directory:
            _publish_at(directory, 'summary.json', raw)
    except OSError:
        raise EvidenceError('Falha na publicação do resumo.') from None
    return destination, summary


def read_run(path):
    """Confere resumo e fechamento; nunca abre caminhos vindos do payload."""
    path = Path(path)
    if (not path.is_absolute() or '..' in path.parts or path.name != 'summary.json'
            or path.parent.parent != EVIDENCE_ROOT / 'runs'):
        raise EvidenceError('Resumo fora da raiz de coleta.')
    try:
        if str(uuid.UUID(path.parent.name)) != path.parent.name:
            raise ValueError()
        with _directory(path.parent) as directory:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SUMMARY_BYTES:
                    raise EvidenceError('Resumo não regular ou excessivo.')
                raw = os.read(descriptor, MAX_SUMMARY_BYTES + 1)
                if len(raw) != info.st_size or len(raw) > MAX_SUMMARY_BYTES:
                    raise EvidenceError('Bytes do resumo divergentes.')
            finally:
                os.close(descriptor)
        summary = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        if (type(summary) is not dict or set(summary) != set(_summary({}, {}, ''))
                or type(summary['schema']) is not int or summary['schema'] != 1):
            raise EvidenceError('Campos de resumo inválidos.')
        reference = _reference(summary['report'])
        if reference['kind'] != 'report':
            raise EvidenceError('Resumo não aponta para relatório.')
        store = EvidenceStore(EVIDENCE_ROOT / 'objects/sha256')
        store.validate(reference)
        payload = store.read(reference)['payload']
        if summary != _summary(payload, reference, path.parent.name) or raw != _canonical(summary) + b'\n':
            raise EvidenceError('Resumo diverge da coleta íntegra.')
        return store, reference, payload
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, MemoryError, RecursionError):
        raise EvidenceError('Resumo ou fechamento inválido.') from None
