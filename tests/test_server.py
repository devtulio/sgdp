# Suíte de testes do backend (server.py) — sobe o servidor real contra um
# banco/uploads/backups temporários e bate nos endpoints REST via http.client.
# python -m unittest discover -s tests   (ou: python tests/test_server.py)
import http.client
import itertools
import json
import os
import shutil
import socketserver
import sqlite3
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

    def criar_usuario(self, username, nome=None, senha='senha123', departamento=None, admin=False, admin_token=None):
        token = admin_token or self.login()
        body = {'username': username, 'nome': nome or username, 'senha': senha, 'admin': admin}
        if departamento:
            body['departamento'] = departamento
        status, created = self.request('POST', '/api/usuarios', body, token=token)
        self.assertEqual(status, 201, created)
        return created

    def upload_pdf(self, token, did, content=b'%PDF-1.4 conteudo de teste', filename='teste.pdf'):
        boundary = 'sgdp-test-boundary'
        body = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="pdf"; filename="{filename}"\r\n'
            f'Content-Type: application/pdf\r\n\r\n'
        ).encode('utf-8') + content + f'\r\n--{boundary}--\r\n'.encode('utf-8')
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=5)
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}', 'Content-Length': str(len(body))}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        conn.request('POST', f'/api/documentos/{did}/arquivo', body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if data else None)


class TestFTSBackfill(unittest.TestCase):
    """Regressão: init_db() precisa indexar documentos pré-existentes na 1ª
    criação de documentos_fts (upgrade de um banco sem FTS5). COUNT(*) numa
    fts5 external-content faz passthrough pra `documentos` e nunca é 0, então
    o backfill não pode depender de COUNT — ver server.py init_db()."""

    def test_documento_preexistente_fica_pesquisavel_apos_init_db(self):
        tmpdir = tempfile.mkdtemp(prefix='sgdp_fts_test_')
        db_path = os.path.join(tmpdir, 'sgdp.db')
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute('''
                CREATE TABLE documentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL,
                    numero INTEGER NOT NULL, ano INTEGER NOT NULL, data TEXT NOT NULL,
                    ementa TEXT NOT NULL, partes TEXT, observacoes TEXT,
                    arquivo_id INTEGER, criado_por INTEGER, atualizado_por INTEGER,
                    criado_em TEXT, atualizado_em TEXT
                )
            ''')
            conn.execute(
                "INSERT INTO documentos (tipo, numero, ano, data, ementa, partes, observacoes) "
                "VALUES ('lei', 1, 2020, '2020-01-01', 'ementa pesquisavel unica', '', '')"
            )
            conn.commit()
            conn.close()

            old_db_path = server.DB_PATH
            server.DB_PATH = db_path
            try:
                server.init_db()
            finally:
                server.DB_PATH = old_db_path

            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT rowid FROM documentos_fts WHERE documentos_fts MATCH 'pesquisavel'"
            ).fetchall()
            conn.close()
            self.assertEqual(rows, [(1,)])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


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


class TestUsuarios(SGDPTestCase):

    def test_criar_usuario_sem_departamento_usa_padrao(self):
        created = self.criar_usuario('u_dep_default')
        self.assertEqual(created['departamento'], 'Procuradoria-Geral')

    def test_criar_usuario_com_departamento_explicito(self):
        created = self.criar_usuario('u_dep_gabinete', departamento='Gabinete')
        self.assertEqual(created['departamento'], 'Gabinete')

    def test_criar_usuario_com_departamento_invalido_retorna_400(self):
        token = self.login()
        status, data = self.request('POST', '/api/usuarios', {
            'username': 'u_dep_invalido', 'nome': 'Invalido', 'senha': 'senha123', 'departamento': 'Financeiro'
        }, token=token)
        self.assertEqual(status, 400)

    def test_endpoint_departamentos_lista_os_dois_fixos(self):
        token = self.login()
        status, data = self.request('GET', '/api/departamentos', token=token)
        self.assertEqual(status, 200)
        self.assertEqual(set(data), {'Procuradoria-Geral', 'Gabinete'})

    def test_criar_usuario_requer_admin(self):
        admin_token = self.login()
        created = self.criar_usuario('u_comum_criar', admin_token=admin_token)
        token_comum = self.login('u_comum_criar', 'senha123')
        status, data = self.request('POST', '/api/usuarios', {
            'username': 'u_nao_deveria_existir', 'nome': 'X', 'senha': 'senha123'
        }, token=token_comum)
        self.assertEqual(status, 403)

    def test_atualizar_usuario_com_departamento_invalido_retorna_400(self):
        admin_token = self.login()
        created = self.criar_usuario('u_dep_update', admin_token=admin_token)
        status, data = self.request('PUT', f"/api/usuarios/{created['id']}", {'departamento': 'Financeiro'}, token=admin_token)
        self.assertEqual(status, 400)

    def test_nao_pode_excluir_o_proprio_usuario(self):
        token = self.login()
        status, data = self.request('GET', '/api/auth/me', token=token)
        status, data = self.request('DELETE', f"/api/usuarios/{data['id']}", token=token)
        self.assertEqual(status, 400)

    def test_excluir_usuario_sem_historico_funciona(self):
        admin_token = self.login()
        created = self.criar_usuario('u_sem_historico', admin_token=admin_token)
        status, data = self.request('DELETE', f"/api/usuarios/{created['id']}", token=admin_token)
        self.assertEqual(status, 200, data)

    def test_excluir_usuario_com_documento_retorna_409_em_vez_de_500(self):
        # Regressão: excluir um usuário que já criou um documento violava a
        # FK de documentos.criado_por (PRAGMA foreign_keys=ON, ligado pelo
        # esqueleto compartilhado) e derrubava um 500 genérico. Deve devolver
        # um 409 explicando o motivo, e o usuário deve continuar existindo
        # (nada de exclusão parcial).
        admin_token = self.login()
        created = self.criar_usuario('u_com_documento', admin_token=admin_token)
        token_novo = self.login('u_com_documento', 'senha123')
        status, doc = self.request('POST', '/api/documentos', {
            'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Documento que impede exclusão', 'assunto': 'Outros'
        }, token=token_novo)
        self.assertEqual(status, 201, doc)

        status, data = self.request('DELETE', f"/api/usuarios/{created['id']}", token=admin_token)
        self.assertEqual(status, 409, data)
        self.assertIn('Desativar', data['error'])

        # usuário não foi apagado pela metade — ainda aparece na listagem
        status, listagem = self.request('GET', '/api/usuarios', token=admin_token)
        self.assertTrue(any(u['id'] == created['id'] for u in listagem))

        # a alternativa sugerida (desativar) funciona
        status, data = self.request('PUT', f"/api/usuarios/{created['id']}", {'ativo': False}, token=admin_token)
        self.assertEqual(status, 200, data)


class TestDocumentosSigilosos(SGDPTestCase):
    """Cobre pode_ver_doc()/pode_editar_doc() — a regra de departamento e sigilo."""

    _contador = itertools.count()

    def setUp(self):
        # setUp roda uma vez por teste, mas todos os testes da classe dividem o
        # mesmo servidor/banco (ver setUpModule) — nomes de usuário são únicos
        # no banco, então cada teste precisa do seu próprio conjunto de usernames.
        suf = next(self._contador)
        self.admin_token = self.login()
        self.user_pg1 = self.criar_usuario(f'pg1_{suf}', departamento='Procuradoria-Geral', admin_token=self.admin_token)
        self.user_pg2 = self.criar_usuario(f'pg2_{suf}', departamento='Procuradoria-Geral', admin_token=self.admin_token)
        self.user_gab = self.criar_usuario(f'gab_{suf}', departamento='Gabinete', admin_token=self.admin_token)
        self.token_pg1 = self.login(f'pg1_{suf}', 'senha123')
        self.token_pg2 = self.login(f'pg2_{suf}', 'senha123')
        self.token_gab = self.login(f'gab_{suf}', 'senha123')

    def _criar_doc(self, token, ementa, sigiloso=False, tipo='oficio'):
        status, doc = self.request('POST', '/api/documentos', {
            'tipo': tipo, 'data': '2026-01-01', 'ementa': ementa, 'assunto': 'Outros', 'sigiloso': sigiloso
        }, token=token)
        self.assertEqual(status, 201, doc)
        return doc

    def test_documento_traz_departamento_de_quem_criou(self):
        doc = self._criar_doc(self.token_gab, 'Doc do Gabinete')
        status, single = self.request('GET', f"/api/documentos/{doc['id']}", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(single['criado_por_departamento'], 'Gabinete')

    def test_documento_sigiloso_invisivel_na_listagem_para_outro_usuario(self):
        self._criar_doc(self.token_pg1, 'Sigiloso do PG1', sigiloso=True)
        status, listado = self.request('GET', '/api/documentos?tipo=oficio', token=self.token_pg2)
        self.assertEqual(status, 200)
        self.assertFalse(any(d['ementa'] == 'Sigiloso do PG1' for d in listado['items']))

    def test_documento_sigiloso_visivel_na_listagem_para_quem_criou(self):
        self._criar_doc(self.token_pg1, 'Sigiloso visivel pro criador', sigiloso=True)
        status, listado = self.request('GET', '/api/documentos?tipo=oficio', token=self.token_pg1)
        self.assertEqual(status, 200)
        self.assertTrue(any(d['ementa'] == 'Sigiloso visivel pro criador' for d in listado['items']))

    def test_documento_sigiloso_visivel_para_admin(self):
        doc = self._criar_doc(self.token_pg1, 'Sigiloso visivel pro admin', sigiloso=True)
        status, single = self.request('GET', f"/api/documentos/{doc['id']}", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(single['ementa'], 'Sigiloso visivel pro admin')

    def test_documento_sigiloso_retorna_404_para_quem_nao_pode_ver(self):
        # 404, não 403 — de propósito, pra não revelar nem que o documento existe.
        doc = self._criar_doc(self.token_pg1, 'Sigiloso 404', sigiloso=True)
        status, data = self.request('GET', f"/api/documentos/{doc['id']}", token=self.token_pg2)
        self.assertEqual(status, 404)

    def test_documento_nao_sigiloso_editavel_por_mesmo_departamento(self):
        doc = self._criar_doc(self.token_pg1, 'Nao sigiloso mesmo depto')
        status, data = self.request('PUT', f"/api/documentos/{doc['id']}", {'ementa': 'Editado pelo PG2'}, token=self.token_pg2)
        self.assertEqual(status, 200, data)

    def test_documento_nao_sigiloso_nao_editavel_por_outro_departamento(self):
        doc = self._criar_doc(self.token_pg1, 'Nao sigiloso outro depto')
        status, data = self.request('PUT', f"/api/documentos/{doc['id']}", {'ementa': 'Editado pelo Gabinete'}, token=self.token_gab)
        self.assertEqual(status, 403, data)

    def test_documento_sigiloso_nao_editavel_nem_por_mesmo_departamento(self):
        doc = self._criar_doc(self.token_pg1, 'Sigiloso mesmo depto', sigiloso=True)
        status, data = self.request('PUT', f"/api/documentos/{doc['id']}", {'ementa': 'Tentativa PG2'}, token=self.token_pg2)
        self.assertEqual(status, 403, data)

    def test_documento_sigiloso_editavel_por_admin(self):
        doc = self._criar_doc(self.token_pg1, 'Sigiloso editado por admin', sigiloso=True)
        status, data = self.request('PUT', f"/api/documentos/{doc['id']}", {'ementa': 'Editado por admin'}, token=self.admin_token)
        self.assertEqual(status, 200, data)

    def test_colega_de_departamento_nao_pode_marcar_documento_como_sigiloso(self):
        doc = self._criar_doc(self.token_pg1, 'Tentativa de marcar sigilo')
        status, data = self.request('PUT', f"/api/documentos/{doc['id']}",
                                     {'ementa': 'Editado', 'sigiloso': True}, token=self.token_pg2)
        self.assertEqual(status, 200, data)  # edição normal passa...
        status, single = self.request('GET', f"/api/documentos/{doc['id']}", token=self.token_pg1)
        self.assertEqual(single['sigiloso'], 0)  # ...mas sigiloso é ignorado, não vira 1

    def test_criador_pode_marcar_seu_proprio_documento_como_sigiloso(self):
        doc = self._criar_doc(self.token_pg1, 'Marcado como sigiloso pelo criador')
        status, data = self.request('PUT', f"/api/documentos/{doc['id']}", {'sigiloso': True}, token=self.token_pg1)
        self.assertEqual(status, 200, data)
        status, single = self.request('GET', f"/api/documentos/{doc['id']}", token=self.token_pg1)
        self.assertEqual(single['sigiloso'], 1)

    def test_dashboard_exclui_sigiloso_de_outro_usuario(self):
        self._criar_doc(self.token_pg1, 'Sigiloso fora do dashboard alheio', sigiloso=True)
        status, dash = self.request('GET', '/api/dashboard', token=self.token_pg2)
        self.assertEqual(status, 200)
        self.assertFalse(any(d['ementa'] == 'Sigiloso fora do dashboard alheio' for d in dash['recentes']))

    def test_dashboard_inclui_sigiloso_para_quem_criou(self):
        self._criar_doc(self.token_pg1, 'Sigiloso dentro do dashboard do criador', sigiloso=True)
        status, dash = self.request('GET', '/api/dashboard', token=self.token_pg1)
        self.assertEqual(status, 200)
        self.assertTrue(any(d['ementa'] == 'Sigiloso dentro do dashboard do criador' for d in dash['recentes']))


class TestVinculos(SGDPTestCase):

    def test_criar_listar_e_excluir_vinculo(self):
        token = self.login()
        status, origem = self.request('POST', '/api/documentos',
                                       {'tipo': 'lei', 'data': '2026-01-01', 'ementa': 'Lei original', 'assunto': 'Outros'}, token=token)
        status, destino = self.request('POST', '/api/documentos',
                                        {'tipo': 'lei', 'data': '2026-06-01', 'ementa': 'Lei que revoga', 'assunto': 'Outros'}, token=token)

        status, listado = self.request('POST', f"/api/documentos/{origem['id']}/vinculos",
                                        {'tipo': 'revoga', 'destino_id': destino['id']}, token=token)
        self.assertEqual(status, 200, listado)
        self.assertEqual(len(listado['items']), 1)
        vid = listado['items'][0]['id']
        self.assertEqual(listado['items'][0]['direcao'], 'direto')
        self.assertEqual(listado['items'][0]['label'], 'Revoga')

        # visto do lado do destino, o vínculo aparece invertido
        status, inverso = self.request('GET', f"/api/documentos/{destino['id']}/vinculos", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(inverso['items'][0]['direcao'], 'inverso')
        self.assertEqual(inverso['items'][0]['label'], 'Revogado por')

        status, _ = self.request('DELETE', f'/api/vinculos/{vid}', token=token)
        self.assertEqual(status, 200)
        status, vazio = self.request('GET', f"/api/documentos/{origem['id']}/vinculos", token=token)
        self.assertEqual(len(vazio['items']), 0)

    def test_vinculo_com_tipo_invalido_retorna_400(self):
        token = self.login()
        status, doc = self.request('POST', '/api/documentos',
                                    {'tipo': 'lei', 'data': '2026-01-01', 'ementa': 'Lei X', 'assunto': 'Outros'}, token=token)
        status, data = self.request('POST', f"/api/documentos/{doc['id']}/vinculos",
                                     {'tipo': 'inexistente', 'destino_id': doc['id']}, token=token)
        self.assertEqual(status, 400)

    def test_vinculo_com_destino_inexistente_retorna_404(self):
        token = self.login()
        status, doc = self.request('POST', '/api/documentos',
                                    {'tipo': 'lei', 'data': '2026-01-01', 'ementa': 'Lei Y', 'assunto': 'Outros'}, token=token)
        status, data = self.request('POST', f"/api/documentos/{doc['id']}/vinculos",
                                     {'tipo': 'revoga', 'destino_id': 999999}, token=token)
        self.assertEqual(status, 404)

    def test_cadeia_normativa_percorre_vinculos_em_cadeia(self):
        token = self.login()
        status, a = self.request('POST', '/api/documentos', {'tipo': 'lei', 'data': '2026-01-01', 'ementa': 'A', 'assunto': 'Outros'}, token=token)
        status, b = self.request('POST', '/api/documentos', {'tipo': 'lei', 'data': '2026-02-01', 'ementa': 'B', 'assunto': 'Outros'}, token=token)
        status, c = self.request('POST', '/api/documentos', {'tipo': 'lei', 'data': '2026-03-01', 'ementa': 'C', 'assunto': 'Outros'}, token=token)
        self.request('POST', f"/api/documentos/{a['id']}/vinculos", {'tipo': 'altera', 'destino_id': b['id']}, token=token)
        self.request('POST', f"/api/documentos/{b['id']}/vinculos", {'tipo': 'altera', 'destino_id': c['id']}, token=token)

        status, cadeia = self.request('GET', f"/api/documentos/{a['id']}/cadeia", token=token)
        self.assertEqual(status, 200, cadeia)
        ids_na_cadeia = {d['id'] for d in cadeia['docs']}
        self.assertEqual(ids_na_cadeia, {a['id'], b['id'], c['id']})
        self.assertEqual(len(cadeia['arestas']), 2)  # sem duplicar arestas


class TestNumeracaoContinua(SGDPTestCase):
    """Lei/Decreto usam contador histórico contínuo (ano sentinela 0 em
    contadores) — não reseta ao mudar de ano, diferente dos outros tipos."""

    _contador = itertools.count()

    def _anos(self):
        # anos exclusivos por teste — evita colisão com contagem absoluta de
        # outros testes que também criam 'oficio' no mesmo banco compartilhado.
        base = 2500 + next(self._contador) * 10
        return base, base + 2

    def test_lei_nao_reinicia_numeracao_ao_mudar_de_ano(self):
        token = self.login()
        ano_a, ano_b = self._anos()
        status, d1 = self.request('POST', '/api/documentos',
                                   {'tipo': 'lei', 'data': f'{ano_a}-01-01', 'ementa': 'Lei ano A', 'ano': ano_a}, token=token)
        self.assertEqual(status, 201, d1)
        status, d2 = self.request('POST', '/api/documentos',
                                   {'tipo': 'lei', 'data': f'{ano_b}-01-01', 'ementa': 'Lei ano B', 'ano': ano_b}, token=token)
        self.assertEqual(status, 201, d2)
        self.assertEqual(d2['numero'], d1['numero'] + 1)

    def test_decreto_nao_reinicia_numeracao_ao_mudar_de_ano(self):
        token = self.login()
        ano_a, ano_b = self._anos()
        status, d1 = self.request('POST', '/api/documentos',
                                   {'tipo': 'decreto', 'data': f'{ano_a}-01-01', 'ementa': 'Decreto ano A', 'ano': ano_a}, token=token)
        status, d2 = self.request('POST', '/api/documentos',
                                   {'tipo': 'decreto', 'data': f'{ano_b}-01-01', 'ementa': 'Decreto ano B', 'ano': ano_b}, token=token)
        self.assertEqual(status, 201, d2)
        self.assertEqual(d2['numero'], d1['numero'] + 1)

    def test_oficio_continua_reiniciando_por_ano_normalmente(self):
        # Contraste com lei/decreto: ofício não é numeração contínua — precisa
        # de um ano exclusivo (não só relativo) pra confirmar que reinicia do 1.
        token = self.login()
        ano_a, ano_b = self._anos()
        self.request('POST', '/api/documentos',
                      {'tipo': 'oficio', 'data': f'{ano_a}-01-01', 'ementa': 'Oficio ano A', 'ano': ano_a}, token=token)
        status, d2 = self.request('POST', '/api/documentos',
                                   {'tipo': 'oficio', 'data': f'{ano_b}-01-01', 'ementa': 'Oficio ano B', 'ano': ano_b}, token=token)
        self.assertEqual(status, 201, d2)
        self.assertEqual(d2['numero'], 1)  # reinicia — não encadeia com o do ano A

    def test_numero_editado_manualmente_recalibra_o_contador(self):
        token = self.login()
        ano_a, ano_b = self._anos()
        numero_alto = 100000 + next(self._contador)
        status, d1 = self.request('POST', '/api/documentos',
                                   {'tipo': 'lei', 'data': f'{ano_a}-01-01', 'ementa': 'Lei numero manual',
                                    'ano': ano_a, 'numero': numero_alto}, token=token)
        self.assertEqual(status, 201, d1)
        self.assertEqual(d1['numero'], numero_alto)
        status, d2 = self.request('POST', '/api/documentos',
                                   {'tipo': 'lei', 'data': f'{ano_b}-01-01', 'ementa': 'Lei proxima automatica', 'ano': ano_b}, token=token)
        self.assertEqual(status, 201, d2)
        self.assertEqual(d2['numero'], numero_alto + 1)


class TestMigracaoOficioInterno(unittest.TestCase):
    """Regressão dedicada da migração de schema que adiciona `oficio_interno`
    (server.py init_db(), branch 'oficio_interno' not in cols): monta um banco
    no formato antigo (documentos sem a coluna, UNIQUE(tipo,numero,ano) só,
    com filhos em tabelas com FK pra documentos) e confere que init_db()
    preserva tudo, sem cascatear DELETE nos filhos nem deixar nenhuma FK de
    outra tabela apontando pra um nome de tabela que deixou de existir."""

    def _montar_banco_antigo(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript('''
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                nome TEXT NOT NULL, senha_hash TEXT NOT NULL, admin INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1,
                criado_em TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE documentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, numero INTEGER NOT NULL,
                ano INTEGER NOT NULL, data TEXT NOT NULL, ementa TEXT NOT NULL, partes TEXT, observacoes TEXT,
                arquivo_id INTEGER, criado_por INTEGER, atualizado_por INTEGER, criado_em TEXT, atualizado_em TEXT,
                UNIQUE(tipo,numero,ano)
            );
            CREATE TABLE lembretes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT NOT NULL, data_prazo TEXT NOT NULL,
                documento_id INTEGER REFERENCES documentos(id) ON DELETE SET NULL, concluido INTEGER DEFAULT 0,
                criado_por INTEGER REFERENCES usuarios(id), criado_em TEXT, notificado_em TEXT
            );
            CREATE TABLE documento_vinculos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origem_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                destino_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL, criado_por INTEGER, criado_em TEXT,
                UNIQUE(origem_id,destino_id,tipo)
            );
            CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE COLLATE NOCASE);
            CREATE TABLE documento_tags (
                documento_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (documento_id, tag_id)
            );
        ''')
        conn.execute("INSERT INTO usuarios (id,username,nome,senha_hash) VALUES (1,'admin','Admin','x')")
        conn.execute("INSERT INTO documentos (id,tipo,numero,ano,data,ementa,criado_por) VALUES (5,'lei',10,2024,'2024-01-01','Lei A',1)")
        conn.execute("INSERT INTO documentos (id,tipo,numero,ano,data,ementa,criado_por) VALUES (7,'lei',11,2024,'2024-02-01','Lei B',1)")
        conn.execute("INSERT INTO documentos (id,tipo,numero,ano,data,ementa,criado_por) VALUES (9,'oficio',1,2026,'2026-01-05','Oficio antigo',1)")
        conn.execute("INSERT INTO documento_vinculos (origem_id,destino_id,tipo,criado_por) VALUES (5,7,'altera',1)")
        conn.execute("INSERT INTO lembretes (titulo,data_prazo,documento_id,criado_por) VALUES ('lembrete antigo','2026-02-01',9,1)")
        conn.execute("INSERT INTO tags (id,nome) VALUES (1,'urgente')")
        conn.execute("INSERT INTO documento_tags (documento_id,tag_id) VALUES (5,1)")
        conn.commit()
        conn.close()

    def test_migracao_preserva_filhos_e_permite_oficio_interno_coexistir(self):
        tmpdir = tempfile.mkdtemp(prefix='sgdp_migracao_test_')
        db_path = os.path.join(tmpdir, 'sgdp.db')
        old_db_path = server.DB_PATH
        try:
            self._montar_banco_antigo(db_path)
            server.DB_PATH = db_path
            server.init_db()
        finally:
            server.DB_PATH = old_db_path

        try:
            conn = sqlite3.connect(db_path)
            conn.execute('PRAGMA foreign_keys=ON')

            # dados antigos preservados com o mesmo id, oficio_interno=0 por padrão
            docs = {r[0]: r[1] for r in conn.execute('SELECT id, oficio_interno FROM documentos').fetchall()}
            self.assertEqual(docs.get(5), 0)
            self.assertEqual(docs.get(7), 0)
            self.assertEqual(docs.get(9), 0)

            # filhos com FK não foram apagados
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM documento_vinculos').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM lembretes').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM documento_tags').fetchone()[0], 1)

            # nenhuma tabela ficou com FK órfã apontando pra um nome que não existe mais
            for nome in ('lembretes', 'documento_vinculos', 'documento_tags'):
                sql = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (nome,)).fetchone()[0]
                self.assertNotIn('documentos_old', sql)
                self.assertNotIn('documentos_new', sql)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

            # inserir um filho novo referenciando um doc antigo funciona com FK ligada
            conn.execute("INSERT INTO lembretes (titulo,data_prazo,documento_id,criado_por) VALUES ('novo','2026-05-01',9,1)")
            conn.commit()

            # Ofício Interno pode coexistir com o Ofício normal de mesmo número/ano
            conn.execute(
                "INSERT INTO documentos (tipo,numero,ano,data,ementa,criado_por,oficio_interno) "
                "VALUES ('oficio',1,2026,'2026-03-01','Oficio interno mesmo numero',1,1)")
            conn.commit()

            # documentos antigos continuam pesquisáveis via FTS5 após a migração
            achados = conn.execute("SELECT rowid FROM documentos_fts WHERE documentos_fts MATCH 'antigo'").fetchall()
            self.assertEqual({r[0] for r in achados}, {9})
            conn.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestOficioInterno(SGDPTestCase):
    """Ofício Interno tem contador próprio por departamento (do criador),
    com sufixo dinâmico, sem colidir com a sequência normal de Ofício."""

    _contador = itertools.count()

    def setUp(self):
        suf = next(self._contador)
        self.admin_token = self.login()
        self.criar_usuario(f'oi_pg_{suf}', departamento='Procuradoria-Geral', admin_token=self.admin_token)
        self.criar_usuario(f'oi_gab_{suf}', departamento='Gabinete', admin_token=self.admin_token)
        self.token_pg = self.login(f'oi_pg_{suf}', 'senha123')
        self.token_gab = self.login(f'oi_gab_{suf}', 'senha123')
        self.ano = 2070 + suf  # ano exclusivo por teste evita interferência entre contadores

    def _criar_oficio(self, token, ementa, oficio_interno=False):
        status, doc = self.request('POST', '/api/documentos', {
            'tipo': 'oficio', 'data': f'{self.ano}-01-01', 'ementa': ementa,
            'ano': self.ano, 'oficio_interno': oficio_interno,
        }, token=token)
        self.assertEqual(status, 201, doc)
        return doc

    def test_oficio_interno_tem_contador_separado_do_oficio_normal(self):
        normal = self._criar_oficio(self.token_pg, 'Oficio normal', oficio_interno=False)
        interno = self._criar_oficio(self.token_pg, 'Oficio interno', oficio_interno=True)
        # ambos começam do 1 na própria sequência — não colidem (UNIQUE inclui oficio_interno)
        self.assertEqual(normal['numero'], 1)
        self.assertEqual(interno['numero'], 1)

    def test_departamentos_diferentes_tem_contadores_de_oficio_interno_independentes(self):
        pg1 = self._criar_oficio(self.token_pg, 'Interno PG 1', oficio_interno=True)
        gab1 = self._criar_oficio(self.token_gab, 'Interno GAB 1', oficio_interno=True)
        pg2 = self._criar_oficio(self.token_pg, 'Interno PG 2', oficio_interno=True)
        self.assertEqual(pg1['numero'], 1)
        self.assertEqual(gab1['numero'], 1)  # departamento diferente, contador próprio
        self.assertEqual(pg2['numero'], 2)   # mesmo departamento, incrementa

    def test_listagem_traz_departamento_do_criador_para_sufixo_no_frontend(self):
        doc = self._criar_oficio(self.token_pg, 'Interno com sufixo', oficio_interno=True)
        status, single = self.request('GET', f"/api/documentos/{doc['id']}", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(single['oficio_interno'], 1)
        self.assertEqual(single['criado_por_departamento'], 'Procuradoria-Geral')

    def test_oficio_interno_editavel_recalibra_contador_do_departamento_do_criador(self):
        doc = self._criar_oficio(self.token_pg, 'Interno numero manual', oficio_interno=True)
        status, atualizado = self.request('PUT', f"/api/documentos/{doc['id']}", {'numero': 50}, token=self.token_pg)
        self.assertEqual(status, 200, atualizado)
        proximo = self._criar_oficio(self.token_pg, 'Interno proximo automatico', oficio_interno=True)
        self.assertEqual(proximo['numero'], 51)


class TestTagsERevisoes(SGDPTestCase):

    def test_tags_do_documento_aparecem_no_endpoint_global(self):
        token = self.login()
        status, doc = self.request('POST', '/api/documentos', {
            'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Doc com tags', 'assunto': 'Outros',
            'tags': ['urgente-teste', 'financeiro-teste'],
        }, token=token)
        self.assertEqual(status, 201, doc)
        self.assertEqual(set(doc['tags']), {'urgente-teste', 'financeiro-teste'})

        status, tags = self.request('GET', '/api/tags', token=token)
        self.assertEqual(status, 200)
        self.assertIn('urgente-teste', tags['items'])

    def test_editar_documento_gera_entrada_no_historico_de_revisoes(self):
        token = self.login()
        status, doc = self.request('POST', '/api/documentos',
                                    {'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Ementa original', 'assunto': 'Outros'}, token=token)
        self.request('PUT', f"/api/documentos/{doc['id']}", {'ementa': 'Ementa editada'}, token=token)

        status, revisoes = self.request('GET', f"/api/documentos/{doc['id']}/revisoes", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(len(revisoes['items']), 1)
        self.assertEqual(revisoes['items'][0]['dados']['ementa'], 'Ementa original')


class TestImportacaoCsv(SGDPTestCase):

    def test_importa_linhas_validas(self):
        token = self.login()
        status, data = self.request('POST', '/api/import/csv', {'rows': [
            {'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'CSV linha 1', 'assunto': 'Outros'},
            {'tipo': 'oficio', 'data': '2026-01-02', 'ementa': 'CSV linha 2', 'assunto': 'Outros'},
        ]}, token=token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data['importados'], 2)
        self.assertEqual(data['erros'], [])

    def test_linha_com_tipo_invalido_vira_erro_sem_derrubar_as_outras(self):
        token = self.login()
        status, data = self.request('POST', '/api/import/csv', {'rows': [
            {'tipo': 'invalido', 'data': '2026-01-01', 'ementa': 'Linha ruim', 'assunto': 'Outros'},
            {'tipo': 'oficio', 'data': '2026-01-03', 'ementa': 'CSV linha boa', 'assunto': 'Outros'},
        ]}, token=token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data['importados'], 1)
        self.assertEqual(len(data['erros']), 1)

    def test_sem_linhas_retorna_400(self):
        token = self.login()
        status, data = self.request('POST', '/api/import/csv', {'rows': []}, token=token)
        self.assertEqual(status, 400)


class TestArquivos(SGDPTestCase):

    def test_upload_e_download_de_pdf(self):
        token = self.login()
        status, doc = self.request('POST', '/api/documentos',
                                    {'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Doc com PDF', 'assunto': 'Outros'}, token=token)
        status, up = self.upload_pdf(token, doc['id'], content=b'%PDF-1.4 conteudo unico de teste')
        self.assertEqual(status, 200, up)
        aid = up['arquivo_id']

        status, single = self.request('GET', f"/api/documentos/{doc['id']}", token=token)
        self.assertEqual(single['arquivo_id'], aid)

        status, baixado = self.request('GET', f'/api/arquivos/{aid}', token=token)
        self.assertEqual(status, 200)
        self.assertIn(b'conteudo unico de teste', baixado)

    def test_download_de_pdf_de_documento_sigiloso_bloqueado_para_outro_usuario(self):
        admin_token = self.login()
        suf = 'arqsig1'
        self.criar_usuario(f'a_{suf}', departamento='Procuradoria-Geral', admin_token=admin_token)
        self.criar_usuario(f'b_{suf}', departamento='Gabinete', admin_token=admin_token)
        token_a = self.login(f'a_{suf}', 'senha123')
        token_b = self.login(f'b_{suf}', 'senha123')

        status, doc = self.request('POST', '/api/documentos', {
            'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Doc sigiloso com PDF', 'assunto': 'Outros', 'sigiloso': True,
        }, token=token_a)
        status, up = self.upload_pdf(token_a, doc['id'])
        aid = up['arquivo_id']

        status, _ = self.request('GET', f'/api/arquivos/{aid}', token=token_b)
        self.assertEqual(status, 404)  # mesma regra de pode_ver_doc: não revela nem que existe

        status, data = self.request('GET', f'/api/arquivos/{aid}', token=token_a)
        self.assertEqual(status, 200)

    def test_upload_requer_permissao_de_edicao_do_documento(self):
        admin_token = self.login()
        suf = 'arqperm1'
        self.criar_usuario(f'a_{suf}', departamento='Procuradoria-Geral', admin_token=admin_token)
        self.criar_usuario(f'b_{suf}', departamento='Gabinete', admin_token=admin_token)
        token_a = self.login(f'a_{suf}', 'senha123')
        token_b = self.login(f'b_{suf}', 'senha123')

        status, doc = self.request('POST', '/api/documentos',
                                    {'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Doc de A', 'assunto': 'Outros'}, token=token_a)
        status, up = self.upload_pdf(token_b, doc['id'])
        self.assertEqual(status, 403, up)


class TestRelatorios(SGDPTestCase):

    def test_relatorio_geral_soma_documentos_do_periodo(self):
        token = self.login()
        self.request('POST', '/api/documentos',
                      {'tipo': 'lei', 'data': '2026-05-15', 'ementa': 'Para relatorio', 'assunto': 'Outros'}, token=token)
        status, data = self.request('GET', '/api/relatorio?de=2026-05-01&ate=2026-05-31', token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(d['ementa'] == 'Para relatorio' for d in data['documentos']))

    def test_relatorio_exclui_sigiloso_de_outro_usuario(self):
        admin_token = self.login()
        suf = 'relsig1'
        self.criar_usuario(f'a_{suf}', admin_token=admin_token)
        self.criar_usuario(f'b_{suf}', admin_token=admin_token)
        token_a = self.login(f'a_{suf}', 'senha123')
        token_b = self.login(f'b_{suf}', 'senha123')
        self.request('POST', '/api/documentos', {
            'tipo': 'lei', 'data': '2026-05-20', 'ementa': 'Sigiloso fora do relatorio', 'assunto': 'Outros', 'sigiloso': True,
        }, token=token_a)
        status, data = self.request('GET', '/api/relatorio?de=2026-05-01&ate=2026-05-31', token=token_b)
        self.assertFalse(any(d['ementa'] == 'Sigiloso fora do relatorio' for d in data['documentos']))

    def test_relatorio_export_csv_retorna_content_type_csv(self):
        token = self.login()
        status, data = self.request('GET', '/api/relatorio/export.csv?de=2026-01-01&ate=2026-12-31', token=token)
        self.assertEqual(status, 200)
        self.assertIn(b'Tipo,N', data if isinstance(data, bytes) else data.encode())

    def test_relatorio_etiquetas_agrupa_por_tag(self):
        token = self.login()
        self.request('POST', '/api/documentos', {
            'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Doc etiquetado', 'assunto': 'Outros', 'tags': ['relatorio-teste'],
        }, token=token)
        status, data = self.request('GET', '/api/relatorio/etiquetas', token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(item['nome'] == 'relatorio-teste' and item['total'] >= 1 for item in data['items']))

    def test_relatorio_integridade_e_admin_only(self):
        admin_token = self.login()
        comum = self.criar_usuario('u_rel_integridade', admin_token=admin_token)
        token_comum = self.login('u_rel_integridade', 'senha123')

        status, data = self.request('GET', '/api/relatorio/integridade', token=token_comum)
        self.assertEqual(status, 403)

        status, data = self.request('GET', '/api/relatorio/integridade', token=admin_token)
        self.assertEqual(status, 200)
        self.assertIn('contagens', data)
        self.assertIn('documentos', data['contagens'])

    def test_contadores_reflete_proximo_numero_disponivel(self):
        token = self.login()
        status, d1 = self.request('POST', '/api/documentos',
                                   {'tipo': 'decreto', 'data': '2026-01-01', 'ementa': 'X', 'ano': 2077}, token=token)
        status, contadores = self.request('GET', '/api/contadores?tipo=decreto&ano=2077', token=token)
        self.assertEqual(status, 200)
        self.assertEqual(contadores['proximo'], d1['numero'] + 1)


class TestConfig(SGDPTestCase):

    def test_atualizar_e_ler_config(self):
        admin_token = self.login()
        status, _ = self.request('PUT', '/api/config', {'orgao_nome': 'Procuradoria de Teste'}, token=admin_token)
        self.assertEqual(status, 200)
        status, data = self.request('GET', '/api/config', token=admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(data['orgao_nome'], 'Procuradoria de Teste')

    def test_atualizar_config_requer_admin(self):
        admin_token = self.login()
        self.criar_usuario('u_cfg_comum', admin_token=admin_token)
        token_comum = self.login('u_cfg_comum', 'senha123')
        status, _ = self.request('PUT', '/api/config', {'orgao_nome': 'Nao deveria'}, token=token_comum)
        self.assertEqual(status, 403)


class TestBackupsDb(SGDPTestCase):

    def test_backup_db_manual_aparece_na_listagem(self):
        admin_token = self.login()
        status, criado = self.request('POST', '/api/backups/db/now', token=admin_token)
        self.assertEqual(status, 200, criado)
        self.assertTrue(criado['ok'])

        status, listagem = self.request('GET', '/api/backups/db', token=admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(any(item['name'] == criado['name'] for item in listagem['items']))


class TestRestoreESincronizacao(SGDPTestCase):
    """Endpoints destrutivos (substituem documentos/arquivos/contadores/signatures
    ou zeram tudo) — cada teste monta seu próprio payload e confere só o que
    escreveu, sem depender de estado deixado por outras classes."""

    def _backup_minimo(self, documentos):
        return {
            'sgdp_version': '1.0.0-teste', 'exported_at': '2026-07-15T00:00:00',
            'documentos': documentos, 'usuarios': [], 'contadores': [], 'arquivos': [], 'signatures': [],
        }

    def test_restore_substitui_completamente_os_documentos(self):
        admin_token = self.login()
        backup = self._backup_minimo([
            {'id': 555001, 'tipo': 'lei', 'numero': 1, 'ano': 2088, 'data': '2088-01-01',
             'ementa': 'Restaurado do backup', 'assunto': 'Outros', 'sigiloso': 0,
             'criado_por': None, 'atualizado_por': None, 'criado_em': '2088-01-01T00:00:00', 'atualizado_em': '2088-01-01T00:00:00'},
        ])
        status, resultado = self.request('POST', '/api/backup/restore', backup, token=admin_token)
        self.assertEqual(status, 200, resultado)
        self.assertEqual(resultado['documentos'], 1)

        status, listado = self.request('GET', '/api/documentos?tipo=lei&ano=2088', token=admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(any(d['ementa'] == 'Restaurado do backup' for d in listado['items']))

        # o que não estava no backup não existe mais
        status, outro_tipo = self.request('GET', '/api/documentos?tipo=oficio', token=admin_token)
        self.assertEqual(outro_tipo['total'], 0)

    def test_restore_requer_admin(self):
        admin_token = self.login()
        self.criar_usuario('u_restore_comum', admin_token=admin_token)
        token_comum = self.login('u_restore_comum', 'senha123')
        status, _ = self.request('POST', '/api/backup/restore', self._backup_minimo([]), token=token_comum)
        self.assertEqual(status, 403)

    def test_sync_apply_insere_documentos_novos_do_backup(self):
        admin_token = self.login()
        backup = self._backup_minimo([
            {'id': 555002, 'tipo': 'decreto', 'numero': 42, 'ano': 2089, 'data': '2089-02-01',
             'ementa': 'Novo via sync-apply', 'assunto': 'Outros', 'atualizado_em': '2089-02-01T00:00:00'},
        ])
        status, resultado = self.request('POST', '/api/backup/sync-apply', {'backup': backup, 'aceitar': []}, token=admin_token)
        self.assertEqual(status, 200, resultado)

        status, listado = self.request('GET', '/api/documentos?tipo=decreto&ano=2089', token=admin_token)
        self.assertTrue(any(d['ementa'] == 'Novo via sync-apply' for d in listado['items']))

    def test_factory_reset_zera_documentos_mas_preserva_usuarios(self):
        admin_token = self.login()
        self.request('POST', '/api/documentos',
                      {'tipo': 'oficio', 'data': '2026-01-01', 'ementa': 'Sera apagado pelo reset', 'assunto': 'Outros'}, token=admin_token)

        status, resultado = self.request('POST', '/api/factory-reset', token=admin_token)
        self.assertEqual(status, 200, resultado)

        status, listado = self.request('GET', '/api/documentos', token=admin_token)
        self.assertEqual(listado['total'], 0)

        # admin sobrevive ao reset — login continua funcionando
        status, data = self.request('POST', '/api/auth/login', {'username': 'admin', 'password': 'admin123'})
        self.assertEqual(status, 200, data)


class TestHealth(SGDPTestCase):

    def test_health_check(self):
        status, data = self.request('GET', '/health')
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])


class TestNuncaEncerraSozinho(SGDPTestCase):

    def test_ultima_sessao_expirar_nao_derruba_o_processo(self):
        # Regressão: existia um modo "Pessoal" em que _check_shutdown() chamava
        # os._exit(0) quando a última sessão ativa expirava. os._exit(0) mata o
        # processo Python na hora, sem exceção capturável — se ainda existisse,
        # o processo deste teste morreria aqui e nada abaixo executaria.
        token = self.login()
        with server.get_db() as conn:
            conn.execute('DELETE FROM sessions')  # simula a última sessão expirando
        server._had_session = True
        server._backup_pos_sess = False
        server._check_shutdown()

        # Se chegou aqui, o processo sobreviveu — confirma que o servidor
        # ainda responde normalmente (não travou nem morreu).
        status, _ = self.request('GET', '/health')
        self.assertEqual(status, 200)

    def test_sessao_sobrevive_atraso_maior_que_o_ttl_antigo(self):
        # Regressão: SESSION_TTL era 15s (renovado pelo ping a cada 5s) — margem
        # curta o bastante para uma sessão expirar sozinha no uso normal (várias
        # chamadas de API concorrentes disputando conexão HTTP logo no login,
        # ou a aba principal perdendo foco ao abrir um popup de documento),
        # derrubando o usuário de volta pro login no meio do trabalho sem
        # ninguém ter saído de propósito.
        #
        # Simula 20s "consumidos" do TTL sem nenhum ping renovar a sessão —
        # sob o TTL antigo (15s) isso já teria expirado; sob o atual (60s)
        # ainda sobra bastante margem.
        self.assertGreater(server.SESSION_TTL, 20,
                            'SESSION_TTL muito curto — sessão expira sozinha em uso normal sem ping')
        token = self.login()
        with server.get_db() as conn:
            conn.execute('UPDATE sessions SET expires=expires-20 WHERE token=?', (token,))
        status, _ = self.request('GET', '/api/documentos', token=token)
        self.assertEqual(status, 200, 'sessão expirou com atraso que o TTL antigo (15s) não sobreviveria')


if __name__ == '__main__':
    unittest.main()
