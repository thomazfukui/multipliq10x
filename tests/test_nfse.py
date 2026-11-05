import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from lxml import etree

from nfse_auto import dps as dps_mod
from nfse_auto import email_gmail, extrator, pipeline
from nfse_auto.certificado import CertificadoA1
from nfse_auto.config import Segredos
from nfse_auto.estado import Estado
from nfse_auto.sefin import NfseAutorizada

RAIZ = Path(__file__).resolve().parent.parent
NS = {"n": dps_mod.NS_NFSE, "ds": "http://www.w3.org/2000/09/xmldsig#"}


@pytest.fixture
def cfg():
    c = yaml.safe_load((RAIZ / "config.example.yaml").read_text(encoding="utf-8"))
    c["aliquota_simples_por_mes"][date.today().strftime("%Y-%m")] = 2.01
    return c


@pytest.fixture
def cliente(cfg):
    return cfg["clientes"][0]


@pytest.fixture(scope="session")
def chave_cert():
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "L.D.T. COMUNICACAO VISUAL LTDA:10947101000128")])
    agora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(nome).issuer_name(nome)
        .public_key(chave.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1)).not_valid_after(agora + timedelta(days=365))
        .sign(chave, hashes.SHA256())
    )
    return chave, cert


@pytest.fixture
def pfx(tmp_path, chave_cert):
    chave, cert = chave_cert
    caminho = tmp_path / "cert.pfx"
    caminho.write_bytes(pkcs12.serialize_key_and_certificates(
        b"teste", chave, cert, None, serialization.BestAvailableEncryption(b"senha123")))
    return str(caminho)


def nota_exemplo(**kw):
    base = dict(numero_dps=7, valor=Decimal("1234.5"), descricao="Comissão\nLinha 2",
                competencia=date(2026, 11, 5), aliquota_simples=Decimal("2.01"),
                emissao=datetime(2026, 11, 5, 10, 0, tzinfo=dps_mod.FUSO_BRASILIA))
    base.update(kw)
    return dps_mod.DadosNota(**base)


# ------------------------------------------------------------------ DPS
def test_id_dps_tem_45_caracteres():
    ident = dps_mod.id_dps("4113700", "10.947.101/0001-28", "1", 7)
    assert ident == "DPS" + "4113700" + "2" + "10947101000128" + "00001" + "000000000000007"
    assert len(ident) == 45


def test_montar_dps_campos(cfg, cliente):
    raiz = dps_mod.montar_dps(cfg, cliente, nota_exemplo())
    g = lambda xp: raiz.findtext(xp, namespaces=NS)  # noqa: E731
    assert g("n:infDPS/n:tpAmb") == "2"  # homologação
    assert g("n:infDPS/n:dhEmi") == "2026-11-05T09:59:00-03:00"
    assert g("n:infDPS/n:prest/n:CNPJ") == "10947101000128"
    assert g("n:infDPS/n:prest/n:regTrib/n:opSimpNac") == "3"
    assert g("n:infDPS/n:prest/n:regTrib/n:regApTribSN") == "1"
    assert g("n:infDPS/n:toma/n:CNPJ") == "61550836000154"
    assert g("n:infDPS/n:toma/n:xNome") == "COOPERATIVA MISTA ROMA"
    assert g("n:infDPS/n:serv/n:cServ/n:cTribNac") == "170601"
    assert g("n:infDPS/n:serv/n:cServ/n:cNBS") == "114061900"
    assert g("n:infDPS/n:serv/n:locPrest/n:cLocPrestacao") == "4113700"
    assert g("n:infDPS/n:valores/n:vServPrest/n:vServ") == "1234.50"
    assert g("n:infDPS/n:valores/n:trib/n:totTrib/n:pTotTribSN") == "2.01"


def test_valor_zero_rejeitado(cfg, cliente):
    with pytest.raises(ValueError):
        dps_mod.montar_dps(cfg, cliente, nota_exemplo(valor=Decimal("0")))


def test_assinatura_valida_e_sem_prefixo(cfg, cliente, chave_cert):
    chave, cert = chave_cert
    a1 = CertificadoA1.de_objetos(chave, cert, [])
    assinado = dps_mod.xml_bytes(dps_mod.assinar_dps(dps_mod.montar_dps(cfg, cliente, nota_exemplo()), a1))
    assert b"<ds:" not in assinado
    raiz = etree.fromstring(assinado)
    sig = raiz.find("ds:Signature", NS)
    assert sig is not None and sig.getparent() is raiz  # assinatura é filha de DPS, após infDPS
    ref = sig.find("ds:SignedInfo/ds:Reference", NS).get("URI")
    assert ref == "#" + raiz.find("n:infDPS", NS).get("Id")
    # Verificação independente com o libxmlsec1 (referência usada por sistemas fiscais)
    xmlsec = pytest.importorskip("xmlsec")
    doc = etree.fromstring(assinado)
    xmlsec.tree.add_ids(doc, ["Id"])
    ctx = xmlsec.SignatureContext()
    ctx.key = xmlsec.Key.from_memory(a1.cert_pem, xmlsec.constants.KeyDataFormatCertPem)
    ctx.verify(xmlsec.tree.find_node(doc, xmlsec.constants.NodeSignature))

    # Qualquer alteração no conteúdo invalida a assinatura
    adulterado = etree.fromstring(assinado.replace(b"<vServ>1234.50<", b"<vServ>9234.50<"))
    xmlsec.tree.add_ids(adulterado, ["Id"])
    with pytest.raises(xmlsec.Error):
        ctx.verify(xmlsec.tree.find_node(adulterado, xmlsec.constants.NodeSignature))


def test_certificado_pfx(pfx):
    a1 = CertificadoA1.de_pfx(pfx, "senha123")
    a1.verificar_validade()
    assert "10947101000128" in a1.titular
    with a1.arquivos_mtls() as (c, k):
        assert Path(c).read_bytes().startswith(b"-----BEGIN CERTIFICATE")
    assert not Path(c).exists() and not Path(k).exists()


# ------------------------------------------------------------------ extração / regras
def rel(**kw):
    base = dict(valor=Decimal("1500.00"), periodo_inicio=None, periodo_fim=date.today() - timedelta(days=5),
                cnpj_tomador="61.550.836/0001-54", instrucoes="", confianca="alta", justificativa="Total da página 2")
    base.update(kw)
    return extrator.DadosRelatorio(**base)


def test_interpretar_aceita_formatos():
    r = extrator.interpretar({"valor_comissao": "1.234,56".replace(".", ""), "periodo_inicio": "",
                              "periodo_fim": "31/10/2026", "cnpj_tomador": "", "instrucoes_faturamento": "",
                              "confianca": "alta", "justificativa": "x"})
    assert r.valor == Decimal("1234.56") and r.periodo_fim == date(2026, 10, 31) and r.periodo_inicio is None


def test_interpretar_sem_valor_falha():
    with pytest.raises(extrator.ErroExtracao):
        extrator.interpretar({"valor_comissao": "", "periodo_inicio": "", "periodo_fim": "2026-10-31",
                              "cnpj_tomador": "", "instrucoes_faturamento": "", "confianca": "baixa",
                              "justificativa": "não achei"})


def test_preparar_dados_ok(cfg, cliente):
    dados, motivos = pipeline.preparar_dados(cfg, cliente, rel(periodo_fim=date(2026, 10, 31)), hoje=date(2026, 11, 5))
    assert motivos == []
    assert "Referente ao período: 01/08/2025 até 31/10/2026" in dados["descricao"]
    assert "Chave Pix: 10.947.101/0001-28" in dados["descricao"]
    assert dados["aliquota_simples"] == "2.01"


def test_preparar_dados_bloqueios(cfg, cliente):
    _, motivos = pipeline.preparar_dados(
        cfg, cliente, rel(valor=Decimal("999999"), confianca="media", cnpj_tomador="11.111.111/0001-11",
                          periodo_fim=date(2026, 12, 31)), hoje=date(2026, 12, 1))
    texto = " ".join(motivos)
    assert "alíquota" in texto.lower() and "Confiança" in texto and "fora do intervalo" in texto
    assert "CNPJ" in texto and "futuro" in texto


def test_numeracao_nunca_repete(tmp_path):
    e = Estado(tmp_path / "e.db")
    assert [e.proximo_numero_dps("1", 10) for _ in range(3)] == [10, 11, 12]
    assert e.proximo_numero_dps("2", 1) == 1


# ------------------------------------------------------------------ fluxo completo (API e e-mail simulados)
class FakeApi:
    emitidas = []

    def __init__(self, ambiente, cert):
        pass

    def emitir(self, xml):
        FakeApi.emitidas.append(xml)
        return NfseAutorizada("4113700CHAVE", "15", b"<NFSe/>")

    def chave_por_id_dps(self, ident):
        return None

    def baixar_danfse(self, chave):
        return b"%PDF-1.4 fake"


@pytest.fixture
def ambiente_fake(monkeypatch, tmp_path, pfx):
    monkeypatch.setattr(pipeline, "DIR_DADOS", tmp_path)
    monkeypatch.setattr(pipeline, "ClienteSefin", FakeApi)
    FakeApi.emitidas = []
    enviados = []
    monkeypatch.setattr(email_gmail, "enviar", lambda *a, **kw: enviados.append((a, kw)) or "<id>")
    seg = Segredos(cert_pfx=pfx, cert_senha="senha123", gmail_usuario="eu@gmail.com", gmail_senha_app="x")
    return seg, Estado(tmp_path / "e.db"), enviados


def email_relatorio(mid="<r1@roma>"):
    return email_gmail.EmailRecebido(message_id=mid, remetente="comissao@consorcioroma.com.br",
                                     assunto="Relatório de comissão", data="", corpo="segue",
                                     pdfs=[("rel.pdf", b"%PDF")])


def test_modo_manual_cria_pendente_e_avisa(cfg, cliente, ambiente_fake, monkeypatch):
    seg, estado, enviados = ambiente_fake
    monkeypatch.setattr(extrator, "extrair", lambda pdfs, corpo: rel())
    nid = pipeline.processar_email(cfg, seg, estado, cliente, email_relatorio())
    assert estado.obter(nid)["status"] == "pendente"
    assert FakeApi.emitidas == []
    assert "Aprovar nota" in enviados[0][0][3] and enviados[0][0][2] == [cfg["email_responsavel"]]

    pipeline.emitir_e_enviar(cfg, seg, estado, nid)
    nota = estado.obter(nid)
    assert nota["status"] == "enviada" and nota["numero_nfse"] == "15"
    assert len(FakeApi.emitidas) == 1
    # homologação: o e-mail da nota vai só para o responsável, nunca para o cliente
    args, kw = enviados[-1]
    assert args[2] == [cfg["email_responsavel"]] and "HOMOLOGAÇÃO" in args[3]
    assert {a[0] for a in kw["anexos"]} == {"NFSe_15.pdf", "NFSe_15.xml"}
    with pytest.raises(ValueError):
        pipeline.emitir_e_enviar(cfg, seg, estado, nid)  # não emite duas vezes


def test_modo_automatico_emite_e_responde_no_fio(cfg, cliente, ambiente_fake, monkeypatch):
    seg, estado, enviados = ambiente_fake
    cfg.update(modo_aprovacao="automatico", ambiente="producao")
    monkeypatch.setattr(extrator, "extrair", lambda pdfs, corpo: rel())
    nid = pipeline.processar_email(cfg, seg, estado, cliente, email_relatorio())
    assert estado.obter(nid)["status"] == "enviada"
    args, kw = enviados[-1]
    assert "comissao@consorcioroma.com.br" in args[2]
    assert cfg["email_responsavel"] in kw["cc"]
    assert kw["em_resposta_a"].message_id == "<r1@roma>"

    # Segundo relatório com o mesmo período → suspeita de duplicidade, fica pendente
    nid2 = pipeline.processar_email(cfg, seg, estado, cliente, email_relatorio("<r2@roma>"))
    assert estado.obter(nid2)["status"] == "pendente"
    assert "duplicidade" in estado.obter(nid2)["motivo"]
    assert len(FakeApi.emitidas) == 1


def test_automatico_com_baixa_confianca_vai_para_aprovacao(cfg, cliente, ambiente_fake, monkeypatch):
    seg, estado, _ = ambiente_fake
    cfg["modo_aprovacao"] = "automatico"
    monkeypatch.setattr(extrator, "extrair", lambda pdfs, corpo: rel(confianca="baixa"))
    nid = pipeline.processar_email(cfg, seg, estado, cliente, email_relatorio())
    assert estado.obter(nid)["status"] == "pendente"
    assert FakeApi.emitidas == []


def test_falha_na_leitura_registra_erro(cfg, cliente, ambiente_fake, monkeypatch):
    seg, estado, enviados = ambiente_fake

    def quebra(pdfs, corpo):
        raise extrator.ErroExtracao("PDF ilegível")

    monkeypatch.setattr(extrator, "extrair", quebra)
    nid = pipeline.processar_email(cfg, seg, estado, cliente, email_relatorio())
    assert estado.obter(nid)["status"] == "erro"
    assert "ERRO" in enviados[-1][0][3]
