# Suíte de testes do backend (server.py) — sobe o servidor real contra um
# banco/uploads/backups temporários e bate nos endpoints REST via http.client.
# python -m unittest discover -s tests   (ou: python tests/test_server.py)
import http.client
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

PORT = 3091
_tmpdir = None
_httpd = None
_thread = None


def setUpModule():
    # Um único servidor para toda a suíte — DB_PATH/UPLOADS_DIR são globais do módulo
    # server.py, então instâncias por classe na mesma porta correm risco de uma classe
    # trocar esses globais enquanto uma thread de requisição da classe anterior ainda
    # está em voo, misturando os dados das duas.
    global _tmpdir, _httpd, _thread
    _tmpdir = tempfile.mkdtemp(prefix='sgdp_test_')
    server.DB_PATH = os.path.join(_tmpdir, 'sgdp.db')
    server.UPLOADS_DIR = os.path.join(_tmpdir, 'uploads')
    server.BACKUP_DIR = os.path.join(_tmpdir, 'backups')
    os.makedirs(server.UPLOADS_DIR, exist_ok=True)
    os.makedirs(server.BACKUP_DIR, exist_ok=True)
    server._modo_servidor = True  # evita os._exit(0) do watchdog em logout
    server.init_db()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    _httpd = socketserver.ThreadingTCPServer(('127.0.0.1', PORT), server.SGDPHandler)
    _thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
    _thread.start()


def tearDownModule():
    _httpd.shutdown()
    _httpd.server_close()
    shutil.rmtree(_tmpdir, ignore_errors=True)


class SGDPTestCase(unittest.TestCase):

    def request(self, method, path, body=None, token=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=5)
        hdrs = {'Content-Type': 'application/json'}
        if token:
            hdrs['Authorization'] = f'Bearer {token}'
        if headers:
            hdrs.update(headers)
        # Content-Length precisa ser em bytes, não em caracteres — corpo com acentos
        # (ex. "Ementa de teste") tem mais bytes que caracteres em UTF-8; passar a
        # string crua deixa o http.client contar caracteres e truncar o corpo na rede.
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = data  # resposta binária (ex: download de arquivo)
        return resp.status, parsed

    def login(self, username='admin', password='admin123'):
        status, data = self.request('POST', '/api/auth/login', {'username': username, 'password': password})
        self.assertEqual(status, 200, data)
        return data['token']


class TestAuth(SGDPTestCase):

    def test_login_com_credenciais_corretas(self):
        status, data = self.request('POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'})
        self.assertEqual(status, 200)
        self.assertIn('token', data)
        self.assertTrue(data['user']['admin'])

    def test_login_com_senha_errada(self):
        status, data = self.request('POST', '/api/auth/login', {'username': 'admin', 'password': 'errada'})
        self.assertEqual(status, 401)

    def test_endpoint_protegido_sem_token(self):
        status, data = self.request('GET', '/api/documentos')
        self.assertEqual(status, 401)

    def test_endpoint_protegido_com_token_invalido(self):
        status, data = self.request('GET', '/api/documentos', token='token-que-nao-existe')
        self.assertEqual(status, 401)

    def test_me_retorna_usuario_da_sessao(self):
        token = self.login()
        status, data = self.request('GET', '/api/auth/me', token=token)
        self.assertEqual(status, 200)
        self.assertEqual(data['username'], 'admin')


class TestDocumentos(SGDPTestCase):

    def test_criar_listar_atualizar_e_excluir_documento(self):
        token = self.login()

        status, created = self.request('POST', '/api/documentos', {
            'tipo': 'lei', 'data': '2026-01-10', 'ementa': 'Ementa de teste', 'assunto': 'Administrativo Geral'
        }, token=token)
        self.assertEqual(status, 201, created)
        did = created['id']
        self.assertEqual(created['ementa'], 'Ementa de teste')

        status, listed = self.request('GET', '/api/documentos?tipo=lei', token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(d['id'] == did for d in listed['items']))

        status, updated = self.request('PUT', f'/api/documentos/{did}', {'ementa': 'Ementa atualizada'}, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(updated['ementa'], 'Ementa atualizada')

        status, single = self.request('GET', f'/api/documentos/{did}', token=token)
        self.assertEqual(status, 200)
        self.assertEqual(single['ementa'], 'Ementa atualizada')

        # soft-delete: some da listagem normal, aparece na lixeira
        status, _ = self.request('DELETE', f'/api/documentos/{did}', token=token)
        self.assertEqual(status, 200)
        status, listed = self.request('GET', '/api/documentos?tipo=lei', token=token)
        self.assertFalse(any(d['id'] == did for d in listed['items']))
        status, trashed = self.request('GET', '/api/lixeira', token=token)
        self.assertTrue(any(d['id'] == did for d in trashed['items']))

        # restaurar da lixeira
        status, _ = self.request('POST', f'/api/lixeira/{did}/restaurar', token=token)
        self.assertEqual(status, 200)
        status, listed = self.request('GET', '/api/documentos?tipo=lei', token=token)
        self.assertTrue(any(d['id'] == did for d in listed['items']))

        # excluir de vez
        self.request('DELETE', f'/api/documentos/{did}', token=token)
        status, _ = self.request('DELETE', f'/api/lixeira/{did}', token=token)
        self.assertEqual(status, 200)
        status, trashed = self.request('GET', '/api/lixeira', token=token)
        self.assertFalse(any(d['id'] == did for d in trashed['items']))

    def test_busca_documento_inexistente_retorna_404(self):
        token = self.login()
        status, data = self.request('GET', '/api/documentos/999999', token=token)
        self.assertEqual(status, 404)

    def test_numeracao_automatica_incrementa_por_tipo_e_ano(self):
        token = self.login()
        status, d1 = self.request('POST', '/api/documentos',
                                   {'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Primeiro', 'ano': 2030}, token=token)
        status, d2 = self.request('POST', '/api/documentos',
                                   {'tipo': 'oficio', 'data': '2026-01-02', 'ementa': 'Segundo', 'ano': 2030}, token=token)
        self.assertEqual(status, 201)
        self.assertEqual(d2['numero'], d1['numero'] + 1)


class TestLembretes(SGDPTestCase):

    def test_criar_concluir_e_excluir_lembrete(self):
        token = self.login()
        status, created = self.request('POST', '/api/lembretes',
                                        {'titulo': 'Lembrete de teste', 'data_prazo': '2026-12-31'}, token=token)
        self.assertEqual(status, 201, created)
        lid = created['id']

        status, updated = self.request('PUT', f'/api/lembretes/{lid}', {'concluido': 1}, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(updated['concluido'], 1)

        status, _ = self.request('DELETE', f'/api/lembretes/{lid}', token=token)
        self.assertEqual(status, 200)


class TestAuditoria(SGDPTestCase):

    def test_criar_documento_gera_registro_de_auditoria(self):
        token = self.login()
        status, created = self.request('POST', '/api/documentos', {
            'tipo': 'decreto', 'data': '2026-02-01', 'ementa': 'Decreto para auditoria'
        }, token=token)
        self.assertEqual(status, 201)

        status, data = self.request('GET', '/api/auditoria', token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(e['acao'] == 'criar' for e in data['items']))


class TestBackup(SGDPTestCase):

    def test_export_backup_json_contem_documentos_criados(self):
        token = self.login()
        self.request('POST', '/api/documentos',
                      {'tipo': 'portaria', 'data': '2026-03-01', 'ementa': 'Portaria para backup'}, token=token)

        status, data = self.request('GET', '/api/backup', token=token)
        self.assertEqual(status, 200)
        self.assertIn('sgdp_version', data)
        self.assertTrue(any(d['ementa'] == 'Portaria para backup' for d in data['documentos']))

    def test_sync_preview_identifica_novo_e_conflito_por_chave_natural(self):
        token = self.login()
        status, created = self.request('POST', '/api/documentos',
                                        {'tipo': 'lei', 'data': '2026-01-10', 'ementa': 'Original', 'assunto': 'Outros'},
                                        token=token)
        backup_fake = {
            'sgdp_version': '1.0.0', 'exported_at': '2026-07-04T00:00:00',
            'documentos': [
                # mesmo tipo/numero/ano do documento local, id diferente (fake) — deve
                # casar pela chave natural, nunca pelo id (ids não são globais entre instalações)
                {'id': 999999, 'tipo': created['tipo'], 'numero': created['numero'], 'ano': created['ano'],
                 'data': created['data'], 'ementa': 'Alterado no backup', 'assunto': 'Educação',
                 'atualizado_em': '2099-01-01T00:00:00'},
                {'id': 999998, 'tipo': 'decreto', 'numero': 999, 'ano': 2099,
                 'data': '2099-01-01', 'ementa': 'Documento novo do backup', 'assunto': 'Outros',
                 'atualizado_em': '2099-01-01T00:00:00'},
            ],
            'usuarios': [], 'contadores': [], 'arquivos': [],
        }
        status, preview = self.request('POST', '/api/backup/sync-preview', backup_fake, token=token)
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview['novos'], 1)
        self.assertEqual(len(preview['conflitos']), 1)
        self.assertEqual(preview['conflitos'][0]['local']['id'], created['id'])


class TestHealth(SGDPTestCase):

    def test_health_check(self):
        status, data = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])


if __name__ == '__main__':
    unittest.main()
