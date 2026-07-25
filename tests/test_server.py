# Suíte de testes do backend (server.py) — sobe o servidor real contra um
# banco/uploads/backups temporários e bate nos endpoints REST via http.client.
# python -m unittest discover -s tests   (ou: python tests/test_server.py)
import http.client
import io
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
import uuid
import zipfile

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
    # Motor de erros: log no dir temporário (não polui o do repositório).
    server._DATA_DIR = _tmpdir
    server._log = server.sgx_base.configurar_log('SGDP', _tmpdir, forcar=True)
    server.init_db()
    # A suíte age como um sistema já instalado, com a senha padrão trocada: sem
    # isto todo login como admin/admin123 tomaria 403, porque o servidor passou a
    # recusar qualquer rota enquanto a troca obrigatória estiver pendente (o
    # bloqueio em si tem teste próprio, em TestSenhaPadraoObrigatoria).
    with server.get_db() as conn:
        conn.execute("UPDATE usuarios SET must_change_password=0 WHERE username='admin'")
        conn.commit()

    # Serve via waitress (mesmo servidor do deploy) para validar o adaptador WSGI.
    import waitress
    app = server.sgx_base._wsgi_app(server.SGDPHandler)
    _httpd = waitress.create_server(app, host='127.0.0.1', port=PORT, threads=8)
    _thread = threading.Thread(target=_httpd.run, daemon=True)
    _thread.start()


def tearDownModule():
    try: _httpd.close()
    except Exception: pass
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

    def test_excluir_lembrete_vai_para_lixeira_em_vez_de_apagar_direto(self):
        token = self.login()
        status, created = self.request('POST', '/api/lembretes',
                                        {'titulo': 'Lembrete pra lixeira', 'data_prazo': '2026-12-31'}, token=token)
        lid = created['id']

        self.request('DELETE', f'/api/lembretes/{lid}', token=token)

        status, listado = self.request('GET', '/api/lembretes', token=token)
        self.assertFalse(any(l['id'] == lid for l in listado['items']))

        status, lixeira = self.request('GET', '/api/lixeira', token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(l['id'] == lid for l in lixeira['lembretes']))

    def test_lembrete_na_lixeira_pode_ser_restaurado(self):
        token = self.login()
        status, created = self.request('POST', '/api/lembretes',
                                        {'titulo': 'Lembrete restauravel', 'data_prazo': '2026-12-31'}, token=token)
        lid = created['id']
        self.request('DELETE', f'/api/lembretes/{lid}', token=token)

        status, _ = self.request('POST', f'/api/lixeira/lembretes/{lid}/restaurar', {}, token=token)
        self.assertEqual(status, 200)

        status, listado = self.request('GET', '/api/lembretes', token=token)
        self.assertTrue(any(l['id'] == lid for l in listado['items']))
        status, lixeira = self.request('GET', '/api/lixeira', token=token)
        self.assertFalse(any(l['id'] == lid for l in lixeira['lembretes']))

    def test_lembrete_na_lixeira_pode_ser_excluido_permanentemente(self):
        token = self.login()
        status, created = self.request('POST', '/api/lembretes',
                                        {'titulo': 'Lembrete pra purgar', 'data_prazo': '2026-12-31'}, token=token)
        lid = created['id']
        self.request('DELETE', f'/api/lembretes/{lid}', token=token)

        status, _ = self.request('DELETE', f'/api/lixeira/lembretes/{lid}', token=token)
        self.assertEqual(status, 200)

        status, lixeira = self.request('GET', '/api/lixeira', token=token)
        self.assertFalse(any(l['id'] == lid for l in lixeira['lembretes']))
        # já não está mais na lixeira — tentar de novo dá 404
        status, _ = self.request('DELETE', f'/api/lixeira/lembretes/{lid}', token=token)
        self.assertEqual(status, 404)


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

    def test_post_auditoria_registra_gerar_documento(self):
        token = self.login()
        status, _ = self.request('POST', '/api/auditoria',
                                  {'detalhes': 'Certidão nº 1/2026'}, token=token)
        self.assertEqual(status, 200)
        status, data = self.request('GET', '/api/auditoria', token=token)
        self.assertEqual(status, 200)
        self.assertTrue(any(e['acao'] == 'gerar_documento'
                            and e['detalhes'] == 'Certidão nº 1/2026' for e in data['items']))


class TestBackup(SGDPTestCase):

    def test_export_backup_json_contem_documentos_criados(self):
        token = self.login()
        self.request('POST', '/api/documentos',
                      {'tipo': 'portaria', 'data': '2026-03-01', 'ementa': 'Portaria para backup'}, token=token)

        status, data = self.request('GET', '/api/backup', token=token)
        self.assertEqual(status, 200)
        self.assertEqual(data.get('_sgx'), 'SGDP')   # envelope padronizado da família
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

    def test_lei_e_decreto_nunca_ficam_sigilosos_mesmo_pedindo(self):
        # Ato normativo só vale se publicado — sigiloso é forçado a 0 na criação...
        for tipo in ('lei', 'decreto'):
            doc = self._criar_doc(self.token_pg1, f'{tipo} tentando sigilo', sigiloso=True, tipo=tipo)
            self.assertEqual(doc['sigiloso'], 0, doc)
            # ...e continua bloqueado na edição, mesmo pelo próprio criador.
            status, atualizado = self.request('PUT', f"/api/documentos/{doc['id']}", {'sigiloso': True}, token=self.token_pg1)
            self.assertEqual(status, 200, atualizado)
            self.assertEqual(atualizado['sigiloso'], 0, atualizado)

    def test_parecer_portaria_oficio_podem_ser_sigilosos(self):
        for tipo in ('parecer', 'portaria', 'oficio'):
            doc = self._criar_doc(self.token_pg1, f'{tipo} sigiloso permitido', sigiloso=True, tipo=tipo)
            self.assertEqual(doc['sigiloso'], 1, doc)


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
            'tipo': 'oficio', 'data': '2026-05-20', 'ementa': 'Sigiloso fora do relatorio', 'assunto': 'Outros', 'sigiloso': True,
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
    """Endpoints destrutivos (substituem documentos/arquivos/contadores ou zeram
    tudo) — cada teste monta seu próprio payload e confere só o que escreveu, sem
    depender de estado deixado por outras classes. O campo 'signatures' antigo é
    mantido no payload de propósito: confere que o restore o ignora sem quebrar."""

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


class TestSigiloEPermissaoLixeira(SGDPTestCase):
    """Regressão do eixo permissão/sigilo (auditoria 2026-07-24).

    O sistema tinha os helpers de visibilidade, mas só 6 rotas os chamavam. As
    demais liam documento sem filtro — e a Lixeira não verificava nada, então
    qualquer procurador apagava em definitivo (com o PDF) o documento sigiloso
    de qualquer outro.
    """

    SEGREDO = 'EMENTA-SIGILOSA-DE-TESTE'

    def _dois_procuradores(self):
        adm = self.login()
        for u in ('sig_a', 'sig_b'):
            self.request('POST', '/api/usuarios',
                         {'username': u, 'nome': u, 'senha': 'senha123', 'admin': False}, token=adm)
        toks = []
        for u in ('sig_a', 'sig_b'):
            st, log = self.request('POST', '/api/auth/login', {'username': u, 'password': 'senha123'})
            self.assertEqual(st, 200, log)
            toks.append(log['token'])
        return adm, toks[0], toks[1]

    def _cria_sigiloso(self, token):
        st, doc = self.request('POST', '/api/documentos', {
            'tipo': 'parecer', 'ementa': self.SEGREDO, 'data': '2026-07-24',
            'ano': 2026, 'sigiloso': True}, token=token)
        self.assertEqual(st, 201, doc)
        self.assertTrue(doc['sigiloso'], 'documento não ficou sigiloso — teste inválido')
        return doc['id']

    def _sem_vazar(self, resposta):
        return self.SEGREDO not in json.dumps(resposta, ensure_ascii=False)

    def test_cadeia_nao_vaza_sigiloso_alheio(self):
        _, a, b = self._dois_procuradores()
        sid = self._cria_sigiloso(a)
        st, resp = self.request('GET', f'/api/documentos/{sid}/cadeia', token=b)
        self.assertEqual(st, 404)
        self.assertTrue(self._sem_vazar(resp))

    def test_cadeia_de_documento_publico_nao_revela_sigiloso_vinculado(self):
        _, a, b = self._dois_procuradores()
        sid = self._cria_sigiloso(a)
        st, pub = self.request('POST', '/api/documentos', {
            'tipo': 'portaria', 'ementa': 'Portaria pública', 'data': '2026-07-24', 'ano': 2026}, token=a)
        self.request('POST', f"/api/documentos/{pub['id']}/vinculos",
                     {'destino_id': sid, 'tipo': 'altera'}, token=a)
        st, resp = self.request('GET', f"/api/documentos/{pub['id']}/cadeia", token=b)
        self.assertEqual(st, 200)
        self.assertTrue(self._sem_vazar(resp), 'vínculo expôs a ementa do sigiloso')
        st, resp = self.request('GET', f"/api/documentos/{pub['id']}/vinculos", token=b)
        self.assertTrue(self._sem_vazar(resp), 'lista de vínculos expôs a ementa do sigiloso')

    def test_revisoes_nao_vazam_sigiloso_alheio(self):
        _, a, b = self._dois_procuradores()
        sid = self._cria_sigiloso(a)
        self.request('PUT', f'/api/documentos/{sid}', {'ementa': self.SEGREDO + ' v2'}, token=a)
        st, resp = self.request('GET', f'/api/documentos/{sid}/revisoes', token=b)
        self.assertEqual(st, 404)
        self.assertTrue(self._sem_vazar(resp))

    def test_lixeira_nao_lista_sigiloso_alheio(self):
        _, a, b = self._dois_procuradores()
        sid = self._cria_sigiloso(a)
        self.request('DELETE', f'/api/documentos/{sid}', token=a)
        st, resp = self.request('GET', '/api/lixeira', token=b)
        self.assertEqual(st, 200)
        self.assertTrue(self._sem_vazar(resp), 'Lixeira expôs documento sigiloso de outro procurador')

    def test_nao_purga_nem_restaura_documento_alheio(self):
        adm, a, b = self._dois_procuradores()
        sid = self._cria_sigiloso(a)
        self.request('DELETE', f'/api/documentos/{sid}', token=a)

        self.assertEqual(self.request('POST', f'/api/lixeira/{sid}/restaurar', token=b)[0], 403)
        self.assertEqual(self.request('DELETE', f'/api/lixeira/{sid}', token=b)[0], 403)
        with server.get_db() as conn:
            ainda = conn.execute('SELECT 1 FROM documentos WHERE id=?', (sid,)).fetchone()
        self.assertIsNotNone(ainda, 'documento alheio foi destruído')

        # o autor e o admin continuam podendo
        self.assertEqual(self.request('POST', f'/api/lixeira/{sid}/restaurar', token=a)[0], 200)
        self.request('DELETE', f'/api/documentos/{sid}', token=a)
        self.assertEqual(self.request('POST', f'/api/lixeira/{sid}/restaurar', token=adm)[0], 200)
        self.request('DELETE', f'/api/documentos/{sid}', token=a)
        self.assertEqual(self.request('DELETE', f'/api/lixeira/{sid}', token=a)[0], 200)


class TestBackupPreservaUsuario(SGDPTestCase):
    """Regressão do eixo perda de dado (auditoria 2026-07-24).

    O export listava 8 das 21 colunas de `usuarios` e o import fazia
    INSERT OR REPLACE com as mesmas 8 — o REPLACE apaga a linha inteira e
    recria, então restaurar um backup zerava e-mail, cpf, cargo, matrícula e
    as 8 smtp_*, inclusive a senha do e-mail pessoal de cada procurador.
    """

    CAMPOS = {'email': 'proc@orindiuva.sp.gov.br', 'cpf': '111.222.333-44',
              'cargo': 'Procuradora-Geral', 'matricula': '9977'}
    SMTP = {'smtp_host': 'smtp.orindiuva.sp.gov.br', 'smtp_port': '587',
            'smtp_user': 'proc@orindiuva.sp.gov.br', 'smtp_pass': 'senha-do-email',
            'smtp_from_name': 'Procuradoria'}

    def _preparar(self, token, uid):
        self.request('PUT', f'/api/usuarios/{uid}', dict(self.CAMPOS), token=token)
        self.request('PUT', '/api/auth/me/smtp', dict(self.SMTP), token=token)

    def _linha(self, uid):
        with server.get_db() as conn:
            return dict(conn.execute('SELECT * FROM usuarios WHERE id=?', (uid,)).fetchone())

    def test_backup_leva_todas_as_colunas_de_usuarios(self):
        token = self.login()
        _, eu = self.request('GET', '/api/auth/me', token=token)
        uid = eu['id'] if isinstance(eu, dict) and 'id' in eu else 1
        self._preparar(token, uid)
        status, backup = self.request('GET', '/api/backup', token=token)
        self.assertEqual(status, 200, backup)
        with server.get_db() as conn:
            colunas = {r[1] for r in conn.execute('PRAGMA table_info(usuarios)')}
        do_backup = set(backup['usuarios'][0])
        # smtp_pass fica de fora de propósito — ver test_backup_nao_leva_senha_de_email
        self.assertEqual(colunas - do_backup, {'smtp_pass'},
                         'backup de usuarios não leva todas as colunas da tabela')

    def test_backup_nao_leva_senha_de_email(self):
        # O arquivo JSON sai do servidor e o manual orienta enviá-lo a outro
        # procurador para sincronizar; a senha do e-mail pessoal é guardada em
        # texto puro e a API nunca a devolve (só smtp_pass_set). Não pode vazar
        # pelo backup — e não precisa: a restauração preserva a que está no banco.
        token = self.login()
        _, eu = self.request('GET', '/api/auth/me', token=token)
        uid = eu['id'] if isinstance(eu, dict) and 'id' in eu else 1
        self._preparar(token, uid)
        _, backup = self.request('GET', '/api/backup', token=token)
        bruto = json.dumps(backup, ensure_ascii=False)
        self.assertNotIn(self.SMTP['smtp_pass'], bruto,
                         'senha do e-mail pessoal vazou no arquivo de backup')
        for u in backup['usuarios']:
            self.assertNotIn('smtp_pass', u)

    def test_restaurar_backup_preserva_smtp_e_dados_do_usuario(self):
        token = self.login()
        _, eu = self.request('GET', '/api/auth/me', token=token)
        uid = eu['id'] if isinstance(eu, dict) and 'id' in eu else 1
        self._preparar(token, uid)
        _, backup = self.request('GET', '/api/backup', token=token)

        status, _ = self.request('POST', '/api/backup/restore', backup, token=token)
        self.assertEqual(status, 200)
        depois = self._linha(uid)
        for campo, valor in {**self.CAMPOS, **self.SMTP}.items():
            self.assertEqual(str(depois.get(campo) or ''), valor,
                             f'{campo} não sobreviveu à restauração do backup')

    def test_backup_antigo_nao_apaga_colunas_que_nao_traz(self):
        # Compatibilidade: arquivo gerado antes da correção só tem 8 colunas.
        # Coluna ausente deve manter o valor atual do banco, não virar nulo.
        token = self.login()
        _, eu = self.request('GET', '/api/auth/me', token=token)
        uid = eu['id'] if isinstance(eu, dict) and 'id' in eu else 1
        self._preparar(token, uid)
        _, backup = self.request('GET', '/api/backup', token=token)
        antigas = ('id', 'username', 'nome', 'senha_hash', 'admin', 'ativo', 'departamento', 'criado_em')
        backup['usuarios'] = [{c: u[c] for c in antigas} for u in backup['usuarios']]

        status, _ = self.request('POST', '/api/backup/restore', backup, token=token)
        self.assertEqual(status, 200)
        depois = self._linha(uid)
        for campo, valor in {**self.CAMPOS, **self.SMTP}.items():
            self.assertEqual(str(depois.get(campo) or ''), valor,
                             f'backup antigo apagou {campo}, que ele nem continha')

    def test_restaurar_preserva_todas_as_colunas_do_documento(self):
        # A lista fixa de colunas do restore cobria 14 das 26 e ia ficando para
        # trás a cada migração: sumiam assunto, o vínculo com o processo, o
        # registro de assinatura, o excluido_em (documento voltava da Lixeira) e
        # o par oficio_interno/oficio_interno_departamento, que faz parte da
        # chave única de numeração.
        token = self.login()
        status, doc = self.request('POST', '/api/documentos', {
            'tipo': 'portaria', 'ementa': 'Designa fiscal de contrato', 'data': '2026-07-24',
            'ano': 2026, 'assunto': 'Pessoal', 'processo_pa': 'PA 123/2026',
            'processo_tipo': 'licitacao', 'processo_ref': 'DL 45/2026',
            'ato_tipo': 'designacao', 'cargo': 'Fiscal de Contrato'}, token=token)
        self.assertEqual(status, 201, doc)
        _, lixo = self.request('POST', '/api/documentos', {
            'tipo': 'parecer', 'ementa': 'Parecer excluído', 'data': '2026-07-24', 'ano': 2026},
            token=token)
        self.request('DELETE', f"/api/documentos/{lixo['id']}", token=token)
        with server.get_db() as conn:
            conn.execute("UPDATE documentos SET assinado_por=1, assinado_em='2026-07-24T10:00:00',"
                         " assinatura_cert='CERT-TESTE' WHERE id=?", (doc['id'],))
            conn.commit()
            antes = {r['id']: dict(r) for r in conn.execute('SELECT * FROM documentos')}

        _, backup = self.request('GET', '/api/backup', token=token)
        self.assertEqual(self.request('POST', '/api/backup/restore', backup, token=token)[0], 200)

        with server.get_db() as conn:
            depois = {r['id']: dict(r) for r in conn.execute('SELECT * FROM documentos')}
        self.assertEqual(depois, antes, 'restaurar backup alterou colunas dos documentos')

    def test_restaurar_backup_gera_ponto_de_recuperacao(self):
        token = self.login()
        _, backup = self.request('GET', '/api/backup', token=token)
        # Limpa os Cofres antes: o nome do backup carimba só até o segundo, e vários
        # testes na mesma seção reescreveriam o mesmo arquivo — sem isso o teste
        # falharia por colisão de nome, não por ausência do ponto de recuperação.
        for f in os.listdir(server.BACKUP_DIR):
            if f.startswith('DB_SGDP_BACKUP_'):
                os.remove(os.path.join(server.BACKUP_DIR, f))
        status, _ = self.request('POST', '/api/backup/restore', backup, token=token)
        self.assertEqual(status, 200)
        self.assertTrue([f for f in os.listdir(server.BACKUP_DIR) if f.startswith('DB_SGDP_BACKUP_')],
                        'restaurar backup não gerou cópia do banco anterior (Cofre .zip)')


class TestSenhaPadraoObrigatoria(SGDPTestCase):
    """Regressão do eixo permissão/sigilo (auditoria 2026-07-24).

    A troca de senha obrigatória existia só no navegador: quem falasse direto
    com a API entrava com a senha padrão (que está no README e no manual) e
    usava o sistema inteiro, rotas de administrador inclusive.
    """

    def _usuario_pendente(self):
        adm = self.login()
        self.request('POST', '/api/usuarios',
                     {'username': 'pendente', 'nome': 'Pendente',
                       'password': 'senha123', 'senha': 'senha123', 'admin': True}, token=adm)
        with server.get_db() as conn:
            uid = conn.execute("SELECT id FROM usuarios WHERE username='pendente'").fetchone()['id']
            conn.execute('UPDATE usuarios SET must_change_password=1 WHERE id=?', (uid,))
            conn.commit()
        st, log = self.request('POST', '/api/auth/login', {'username': 'pendente', 'password': 'senha123'})
        self.assertEqual(st, 200, log)
        return log['token'], uid

    def test_api_recusa_enquanto_a_senha_nao_for_trocada(self):
        tok, _ = self._usuario_pendente()
        for rota in ('/api/documentos', '/api/usuarios', '/api/backup'):
            st, _ = self.request('GET', rota, token=tok)
            self.assertEqual(st, 403, f'{rota} respondeu {st} com a senha padrão pendente')

    def test_libera_o_que_a_tela_de_troca_precisa(self):
        tok, uid = self._usuario_pendente()
        self.assertEqual(self.request('GET', '/api/auth/me', token=tok)[0], 200)
        st, _ = self.request('PUT', f'/api/usuarios/{uid}', {'senha': 'TrocadaAgora#2026'}, token=tok)
        self.assertEqual(st, 200, 'não deu para trocar a própria senha')
        st, log = self.request('POST', '/api/auth/login',
                               {'username': 'pendente', 'password': 'TrocadaAgora#2026'})
        self.assertEqual(st, 200)
        self.assertEqual(self.request('GET', '/api/documentos', token=log['token'])[0], 200,
                         'sistema continuou bloqueado depois de trocar a senha')


class TestConflitoDeEdicao(SGDPTestCase):
    """Eixo concorrência (auditoria 2026-07-24).

    Três procuradores usam o mesmo acervo. Sem detecção de conflito, dois
    editando o mesmo documento recebiam 200 e a última gravação apagava o
    texto da outra, em silêncio — medido antes da correção.
    """

    def _doc(self, token):
        st, d = self.request('POST', '/api/documentos', {
            'tipo': 'parecer', 'ementa': 'Original', 'data': '2026-07-24', 'ano': 2026}, token=token)
        self.assertEqual(st, 201, d)
        return d['id'], d['atualizado_em']

    def test_edicao_a_partir_de_versao_velha_e_recusada(self):
        token = self.login()
        did, base = self._doc(token)
        st, _ = self.request('PUT', f'/api/documentos/{did}',
                             {'ementa': 'Versão A', '_baseUpdatedAt': base}, token=token)
        self.assertEqual(st, 200)
        st, resp = self.request('PUT', f'/api/documentos/{did}',
                                {'ementa': 'Versão B', '_baseUpdatedAt': base}, token=token)
        self.assertEqual(st, 409, 'segunda gravação sobrescreveu a primeira')
        self.assertIn('current', resp)
        st, atual = self.request('GET', f'/api/documentos/{did}', token=token)
        self.assertEqual(atual['ementa'], 'Versão A', 'o texto da primeira pessoa foi perdido')

    def test_recarregar_e_salvar_funciona(self):
        token = self.login()
        did, base = self._doc(token)
        self.request('PUT', f'/api/documentos/{did}', {'ementa': 'V1', '_baseUpdatedAt': base}, token=token)
        st, atual = self.request('GET', f'/api/documentos/{did}', token=token)
        st, _ = self.request('PUT', f'/api/documentos/{did}',
                             {'ementa': 'V2', '_baseUpdatedAt': atual['atualizado_em']}, token=token)
        self.assertEqual(st, 200, 'recarregar e salvar deveria funcionar')

    def test_sem_base_continua_gravando(self):
        # Retrocompatível: cliente que não envia a base segue funcionando.
        token = self.login()
        did, _ = self._doc(token)
        st, _ = self.request('PUT', f'/api/documentos/{did}', {'ementa': 'Sem base'}, token=token)
        self.assertEqual(st, 200)


class TestNumeracaoSimultanea(SGDPTestCase):
    """Eixo concorrência (auditoria 2026-07-24).

    Dois procuradores criando ao mesmo tempo liam o mesmo "próximo número" e o
    segundo tomava 409, tendo de repetir tudo à mão — em 36 criações paralelas,
    4 caíam. Quando o sistema é quem atribui o número, ele agora tenta o
    seguinte sozinho; quando o usuário digitou o número, o 409 continua.
    """

    def test_criacoes_simultaneas_nao_perdem_documento(self):
        import threading
        token = self.login()
        resultados, trava = [], threading.Lock()

        def cria(i):
            st, d = self.request('POST', '/api/documentos', {
                'tipo': 'portaria', 'ementa': f'Simultânea {i}',
                'data': '2026-07-24', 'ano': 2026}, token=token)
            with trava:
                resultados.append((st, d.get('numero')))

        ths = [threading.Thread(target=cria, args=(i,)) for i in range(6)]
        for t in ths: t.start()
        for t in ths: t.join()

        criados = [n for st, n in resultados if st == 201]
        self.assertEqual(len(criados), 6, f'documentos perdidos por colisão: {resultados}')
        self.assertEqual(len(set(criados)), 6, f'números duplicados: {sorted(criados)}')

    def test_numero_escolhido_pelo_usuario_ainda_recusa(self):
        # Aqui a colisão é informação, não sorteio: o usuário precisa saber que
        # aquele número já existe, em vez de receber outro em silêncio.
        token = self.login()
        st, _ = self.request('POST', '/api/documentos', {
            'tipo': 'decreto', 'numero': 777, 'ementa': 'Primeiro',
            'data': '2026-07-24', 'ano': 2026}, token=token)
        self.assertEqual(st, 201)
        st, resp = self.request('POST', '/api/documentos', {
            'tipo': 'decreto', 'numero': 777, 'ementa': 'Repetido',
            'data': '2026-07-24', 'ano': 2026}, token=token)
        self.assertEqual(st, 409, 'número digitado pelo usuário não deveria ser trocado sozinho')
        self.assertIn('777', resp.get('error', ''))


class TestSenhaPadraoMarcadaNoBoot(SGDPTestCase):
    """Regressão do eixo permissão/sigilo (auditoria 2026-07-24).

    Quem instalou antes da coluna must_change_password existir recebeu 0 pelo
    DEFAULT do ALTER TABLE: ficou com a senha do manual e sem o bloqueio do
    servidor, porque a marca de troca só é gravada na criação do admin. O boot
    precisa remarcar quem continua na senha padrão.
    """

    def _limpa(self):
        with server.get_db() as conn:
            conn.execute("DELETE FROM usuarios WHERE username='antigo'")
            conn.execute("UPDATE usuarios SET must_change_password=0 WHERE username='admin'")
            conn.commit()

    def _cria_e_reinicia(self, senha):
        self.addCleanup(self._limpa)
        with server.get_db() as conn:
            conn.execute(
                'INSERT INTO usuarios (username,nome,senha_hash,admin,ativo,must_change_password)'
                ' VALUES (?,?,?,0,1,0)',
                ('antigo', 'Instalacao antiga', server._hash_password(senha)))
            conn.commit()
        server.init_db()   # o que acontece a cada início do servidor
        with server.get_db() as conn:
            return conn.execute(
                "SELECT must_change_password FROM usuarios WHERE username='antigo'"
            ).fetchone()['must_change_password']

    def test_boot_marca_quem_ficou_na_senha_padrao(self):
        self.assertEqual(self._cria_e_reinicia('admin123'), 1,
                         'conta com a senha padrão seguiu sem exigir troca')

    def test_boot_nao_mexe_em_quem_ja_trocou(self):
        self.assertEqual(self._cria_e_reinicia('OutraSenha#2026'), 0,
                         'exigiu troca de quem já tinha saído da senha padrão')


class TestBackupPadronizado(SGDPTestCase):
    """Padronização do fluxo de backup (2026-07): envelope único _sgx/schema/
    exportedAt, Cofre .zip com anexos, leitura retrocompatível dos formatos antigos.
    Endpoints destrutivos — cada teste reconstrói o que precisa e confere só isso."""

    def _raw(self, method, path, data, token):
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=15)
        hdrs = {'Content-Length': str(len(data))}
        if token: hdrs['Authorization'] = f'Bearer {token}'
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse(); body = resp.read(); conn.close()
        try: return resp.status, json.loads(body)
        except ValueError: return resp.status, body

    def _doc_com_pdf(self, token):
        st, doc = self.request('POST', '/api/documentos',
                               {'tipo': 'parecer', 'data': '2026-07-24', 'ementa': 'Doc do Cofre', 'assunto': 'Outros'}, token=token)
        self.assertEqual(st, 201, doc)
        st, _ = self.upload_pdf(token, doc['id'], content=b'%PDF-1.4 anexo do cofre')
        self.assertEqual(st, 200)
        return doc['id']

    def test_export_tem_envelope_novo_sem_segredo(self):
        token = self.login()
        with server.get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO sys_settings VALUES ('smtp_pass','SEGREDO')")
            conn.commit()
        st, j = self.request('GET', '/api/backup', token=token)
        self.assertEqual(st, 200)
        self.assertEqual(j.get('_sgx'), 'SGDP')
        self.assertEqual(j.get('schema'), server._BACKUP_SCHEMA)
        self.assertIn('exportedAt', j)
        self.assertNotIn('smtp_pass', j.get('settings', {}))
        self.assertTrue(j.get('usuarios'), 'SGDP deve levar usuarios no JSON')
        self.assertTrue(all('smtp_pass' not in u for u in j['usuarios']))

    def test_cofre_e_zip_com_anexos_e_restaura(self):
        token = self.login()
        self._doc_com_pdf(token)
        st, d = self.request('POST', '/api/backups/db/now', {}, token=token)
        self.assertEqual(st, 200, d)
        self.assertTrue(d['name'].endswith('.zip'), d['name'])
        st, raw = self.request('GET', f"/api/backups/db/download?name={d['name']}", token=token)
        self.assertEqual(st, 200)
        self.assertEqual(raw[:4], b'PK\x03\x04')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            nomes = z.namelist()
        self.assertIn('banco.db', nomes)
        self.assertTrue(any(n.startswith('uploads/') for n in nomes), nomes)

        # zera tudo (inclui apagar uploads) e restaura o pacote → docs + anexo voltam
        self.assertEqual(self.request('POST', '/api/factory-reset', {}, token=token)[0], 200)
        self.assertEqual(self._raw('POST', '/api/backups/db/restore', raw, token)[0], 200)
        st, listado = self.request('GET', '/api/documentos?tipo=parecer', token=token)
        self.assertTrue(any(x['ementa'] == 'Doc do Cofre' for x in listado['items']))
        with server.get_db() as conn:
            disco = conn.execute('SELECT nome_disco FROM arquivos ORDER BY id DESC').fetchone()['nome_disco']
        self.assertTrue(os.path.isfile(os.path.join(server.UPLOADS_DIR, disco)), 'anexo não voltou ao disco')

    def test_restore_aceita_db_legado_sem_mexer_nos_anexos(self):
        token = self.login()
        self._doc_com_pdf(token)
        # um .db cru (formato antigo do Cofre), gerado do banco atual. Fecha as
        # conexões à mão: o `with sqlite3.connect` encerra a transação, não a conexão,
        # e no Windows o arquivo aberto bloqueia o os.remove.
        legado = os.path.join(server.BACKUP_DIR, 'legado_teste.db')
        s = sqlite3.connect(server.DB_PATH); k = sqlite3.connect(legado)
        try:
            with k: s.backup(k)
        finally:
            s.close(); k.close()
        with open(legado, 'rb') as f:
            db_bytes = f.read()
        os.remove(legado)
        antes = set(os.listdir(server.UPLOADS_DIR))
        st, d = self._raw('POST', '/api/backups/db/restore', db_bytes, token)
        self.assertEqual(st, 200, d)
        self.assertEqual(set(os.listdir(server.UPLOADS_DIR)), antes, '.db legado não deve tocar nos uploads')

    def test_envelope_antigo_ainda_restaura(self):
        token = self.login()
        antigo = {'sgdp_version': '1.0.0', 'exported_at': '2025-01-01T00:00:00',
                  'documentos': [{'tipo': 'lei', 'numero': 7, 'ano': 2099, 'data': '2099-01-01',
                                  'ementa': 'Do envelope antigo', 'assunto': 'Outros', 'sigiloso': 0}],
                  'usuarios': [], 'contadores': [], 'arquivos': []}
        st, d = self.request('POST', '/api/backup/restore', antigo, token=token)
        self.assertEqual(st, 200, d)
        st, listado = self.request('GET', '/api/documentos?tipo=lei&ano=2099', token=token)
        self.assertTrue(any(x['ementa'] == 'Do envelope antigo' for x in listado['items']))

    def test_arquivos_invalidos_recusados(self):
        token = self.login()
        self.assertEqual(self.request('POST', '/api/backup/restore', {'foo': 1}, token=token)[0], 400)
        self.assertEqual(self._raw('POST', '/api/backups/db/restore', b'lixo qualquer', token)[0], 400)


class TestRecusaSenhaPadrao(SGDPTestCase):
    """Não deixa definir a senha de fábrica como NOVA senha, nos dois caminhos de
    troca do SGDP (PUT /api/usuarios e PUT /api/auth/senha). Ver sgx_base.eh_senha_padrao."""

    def test_update_usuario_recusa_padrao(self):
        tok = self.login()
        with server.get_db() as conn:
            uid = conn.execute("SELECT id FROM usuarios WHERE username='admin'").fetchone()['id']
        st, r = self.request('PUT', f'/api/usuarios/{uid}', {'senha': 'admin123'}, token=tok)
        self.assertEqual(st, 400, r)
        self.assertIn('padrão', (r or {}).get('error', ''))

    def test_auth_senha_recusa_padrao(self):
        tok = self.login()
        st, r = self.request('PUT', '/api/auth/senha',
                             {'atual': 'admin123', 'nova': 'admin123', 'confirma': 'admin123'}, token=tok)
        self.assertEqual(st, 400, r)
        self.assertIn('padrão', (r or {}).get('error', ''))


class TestMotorErros(SGDPTestCase):
    """Motor de captura e tratamento de erros (portado do piloto SGCD)."""

    def _raw(self, method, path, data, token=None):
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
        hdrs = {'Content-Type': 'application/json'}
        if token: hdrs['Authorization'] = f'Bearer {token}'
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse(); body = resp.read(); conn.close()
        try: return resp.status, json.loads(body) if body else None
        except ValueError: return resp.status, body

    def test_param_invalido_400(self):
        tok = self.login()
        self.assertEqual(self.request('GET', '/api/documentos?per=abc', token=tok)[0], 400)

    def test_log_client_sem_auth_204(self):
        st, _ = self._raw('POST', '/api/log/client',
                          json.dumps({'msg': 'boom teste', 'view': 'view-x'}).encode())
        self.assertEqual(st, 204)

    def test_log_client_chega_no_log_e_diagnostico(self):
        tok = self.login()
        marca = f'erro-teste-{uuid.uuid4().hex[:8]}'
        self._raw('POST', '/api/log/client', json.dumps({'msg': marca, 'view': 'view-y'}).encode())
        caminho = server.sgx_base.caminho_log_erros(server._DATA_DIR, 'SGDP')
        with open(caminho, encoding='utf-8', errors='replace') as f:
            self.assertIn(marca, f.read())
        st, d = self.request('GET', '/api/diagnostico/erros', token=tok)
        self.assertEqual(st, 200)
        self.assertTrue(any('cliente-js' in g.get('tipo', '') for g in d['erros']))

    def test_diagnostico_so_admin(self):
        admin = self.login()
        self.request('POST', '/api/usuarios', {'username': 'u_diag_dp', 'nome': 'U', 'senha': 'senha123',
                                               'departamento': 'Procuradoria-Geral'}, token=admin)
        comum = self.request('POST', '/api/auth/login', {'username': 'u_diag_dp', 'password': 'senha123'})[1]['token']
        self.assertEqual(self.request('GET', '/api/diagnostico/erros', token=comum)[0], 403)


if __name__ == '__main__':
    unittest.main()
