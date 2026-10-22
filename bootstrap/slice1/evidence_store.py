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
        pending = '.pending-' + uuid.uuid4().hex
        try:
            with _directory(self.root) as directory:
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
                        os.link(pending, reference['stored_sha256'] + '.json.gz', src_dir_fd=directory,
                                dst_dir_fd=directory, follow_symlinks=False)
                    except FileExistsError:
                        if _read_at(directory, reference) != data:
                            raise EvidenceError('Objeto existente diverge dos bytes esperados.')
                    os.fsync(directory)
                finally:
                    if created:
                        os.unlink(pending, dir_fd=directory)
        except OSError:
            raise EvidenceError('Falha na publicação atômica da evidência.') from None
        return reference
