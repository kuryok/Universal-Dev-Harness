"""Consulta modelos gratuitos públicos, sem autenticação pessoal ou dados reais."""
import datetime
import http.client
import json
from pathlib import Path
import re
import ssl
import time
import uuid

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'evidence/public-free-probe.json'
HOST = 'opencode.ai'
PATH = '/inference/openai/v1/chat/completions'
MODELS = ('deepseek-v4.1-flash-free',)


def main():
    """Limita chamadas e registra somente campos permitidos das respostas."""
    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else []
    if any(record.get('model') in MODELS for record in previous):
        raise RuntimeError('Modelo já consultado; nova tentativa do mesmo modelo bloqueada')
    if sum(record.get('kind') == 'attempt' for record in previous) >= 3:
        raise RuntimeError('Limite de três tentativas já atingido; evidências preservadas')
    previous.append({'kind': 'source', 'installed_version': '1.18.35',
                     'documentation': 'https://opencode.ai/v2/docs/console/models/',
                     'public_catalog': 'https://opencode.ai/zen/v1/models',
                     'catalog_confirmed_model': 'deepseek-v4.1-flash-free',
                     'documented_price_input_output': 'Free / Free',
                     'authorization_scope': 'Nova tentativa específica autorizada pelo usuário; Big Pickle recusado permanece evidência separada',
                     'endpoint': 'https://' + HOST + PATH,
                     'public_authorization_origin': 'Fallback apiKey public do binário instalado; nenhuma credencial pessoal',
                     'documentation_may_differ_from_installed_version': True,
                     'tls_verified': True, 'personal_credentials_read': False,
                     'prompt': 'Responda UDH_REAL_PROBE_OK', 'max_tokens': 64})
    for model in MODELS:
        if sum(record.get('kind') == 'attempt' for record in previous) >= 3:
            break
        if any(record.get('model') == model for record in previous):
            continue
        body = json.dumps({'model': model, 'messages': [
            {'role': 'user', 'content': 'Responda UDH_REAL_PROBE_OK'}],
            'max_tokens': 64, 'stream': False}).encode()
        record = {'kind': 'attempt', 'model': model,
                  'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        connection = http.client.HTTPSConnection(HOST, timeout=20,
                                                 context=ssl.create_default_context())
        start = time.monotonic()
        try:
            session = 'ses_' + uuid.uuid4().hex
            connection.request('POST', PATH, body=body, headers={
                'Content-Type': 'application/json', 'Accept': 'application/json',
                'Authorization': 'Bearer public',
                'User-Agent': 'opencode/1.18.35',
                'x-opencode-client': 'cli', 'x-opencode-session': session,
                'x-opencode-session-id': session,
                'x-opencode-request': 'msg_' + uuid.uuid4().hex})
            response = connection.getresponse()
            record['http_status'] = response.status
            payload = response.read(65537)
            record['response_bytes_capped'] = len(payload)
            try:
                parsed = json.loads(payload)
            except (ValueError, UnicodeError):
                parsed = {}
            error = parsed.get('error') if isinstance(parsed, dict) else None
            if isinstance(error, dict):
                for field in ('code', 'type'):
                    value = error.get(field)
                    if isinstance(value, str) and re.fullmatch('[A-Za-z0-9_.:-]{1,100}', value):
                        record['error_' + field] = value
                message = str(error.get('message', '')).lower()
                if any(term in message for term in ('api key', 'api_key', 'authentication', 'credential')):
                    record['error_reason_category'] = 'credential_required'
                elif any(term in message for term in ('account', 'billing', 'balance', 'credit', 'payment')):
                    record['error_reason_category'] = 'account_or_billing_required'
                elif any(term in message for term in ('captcha', 'challenge', 'client')):
                    record['error_reason_category'] = 'client_context_or_challenge'
                elif error.get('type') == 'FreeTierError':
                    record['error_reason_category'] = 'free_tier_rejected'
            record['marker_received'] = b'UDH_REAL_PROBE_OK' in payload
            record['returned_choices'] = isinstance(parsed, dict) and bool(parsed.get('choices'))
            record['result'] = 'passou' if response.status == 200 and record['marker_received'] else 'falhou'
        except (OSError, http.client.HTTPException) as error:
            record.update(result='falhou', exception_type=type(error).__name__)
        finally:
            connection.close()
            record['elapsed_seconds'] = round(time.monotonic() - start, 3)
            previous.append(record)
            OUTPUT.parent.mkdir(exist_ok=True)
            OUTPUT.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if record.get('result') == 'passou' or record.get('http_status') in (401, 403, 429):
            break


if __name__ == '__main__':
    main()
