"""Montagem e assinatura da DPS (Declaração de Prestação de Serviço) — padrão NFS-e Nacional."""
from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import __version__
from .certificado import CertificadoA1

NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"
FUSO_BRASILIA = timezone(timedelta(hours=-3))

AMBIENTE = {"producao": "1", "homologacao": "2"}


@dataclass
class DadosNota:
    """Tudo o que muda de uma nota para outra."""

    numero_dps: int
    valor: Decimal
    descricao: str
    competencia: date
    aliquota_simples: Decimal
    emissao: datetime | None = None


def so_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def formatar_decimal(valor: Decimal, casas: int = 2) -> str:
    q = Decimal(1).scaleb(-casas)
    return str(Decimal(valor).quantize(q, rounding=ROUND_HALF_UP))


def id_dps(municipio_ibge: str, cnpj: str, serie: str, numero: int) -> str:
    """Id = "DPS" + cLocEmi(7) + tpInsc(1: 2=CNPJ) + inscrição(14) + série(5) + nDPS(15)."""
    return (
        "DPS"
        + so_digitos(municipio_ibge).zfill(7)
        + "2"
        + so_digitos(cnpj).zfill(14)
        + so_digitos(serie).zfill(5)
        + str(numero).zfill(15)
    )


def _sub(pai, tag, texto=None):
    el = etree.SubElement(pai, f"{{{NS_NFSE}}}{tag}")
    if texto is not None:
        el.text = str(texto)
    return el


def montar_dps(cfg: dict, cliente: dict, nota: DadosNota) -> etree._Element:
    prest = cfg["prestador"]
    toma = cliente["tomador"]
    serv = cliente["servico"]
    dps_cfg = cfg["dps"]

    if not nota.valor or nota.valor <= 0:
        raise ValueError("Valor do serviço deve ser maior que zero.")
    if len(nota.descricao) > 2000:
        raise ValueError("Descrição do serviço passa de 2000 caracteres.")

    # dhEmi não pode estar no futuro em relação ao servidor da Receita; recua 1 minuto.
    emissao = (nota.emissao or datetime.now(FUSO_BRASILIA)) - timedelta(minutes=1)
    emissao = emissao.astimezone(FUSO_BRASILIA).replace(microsecond=0)

    ident = id_dps(prest["municipio_ibge"], prest["cnpj"], dps_cfg["serie"], nota.numero_dps)

    raiz = etree.Element(f"{{{NS_NFSE}}}DPS", nsmap={None: NS_NFSE})
    raiz.set("versao", str(dps_cfg.get("versao_layout", "1.00")))
    inf = _sub(raiz, "infDPS")
    inf.set("Id", ident)

    _sub(inf, "tpAmb", AMBIENTE[cfg["ambiente"]])
    _sub(inf, "dhEmi", emissao.isoformat())
    _sub(inf, "verAplic", f"nfse_auto-{__version__}")
    _sub(inf, "serie", so_digitos(dps_cfg["serie"]))
    _sub(inf, "nDPS", nota.numero_dps)
    _sub(inf, "dCompet", nota.competencia.isoformat())
    _sub(inf, "tpEmit", "1")  # 1 = Prestador
    # Em 11/2026 os ambientes divergem: a Produção Restrita já valida o leiaute da reforma
    # tributária (exige finNFSe, recusa cNBS em cServ); a produção ainda usa o leiaute antigo.
    leiaute_novo = cfg["ambiente"] == "homologacao"
    if leiaute_novo:
        _sub(inf, "finNFSe", "0")  # 0 = NFS-e regular
    _sub(inf, "cLocEmi", prest["municipio_ibge"])

    # Prestador: quando ele mesmo é o emitente, só CNPJ + regime (nome/endereço vêm do cadastro).
    p = _sub(inf, "prest")
    _sub(p, "CNPJ", so_digitos(prest["cnpj"]))
    if prest.get("inscricao_municipal"):
        _sub(p, "IM", prest["inscricao_municipal"])
    reg = _sub(p, "regTrib")
    _sub(reg, "opSimpNac", prest["op_simples_nacional"])
    if int(prest["op_simples_nacional"]) == 3:
        _sub(reg, "regApTribSN", prest["regime_apuracao_sn"])
    _sub(reg, "regEspTrib", prest.get("regime_especial", 0))

    t = _sub(inf, "toma")
    _sub(t, "CNPJ", so_digitos(toma["cnpj"]))
    _sub(t, "xNome", toma["razao_social"])
    if toma.get("telefone"):
        _sub(t, "fone", so_digitos(toma["telefone"]))
    if toma.get("email"):
        _sub(t, "email", toma["email"])

    s = _sub(inf, "serv")
    loc = _sub(s, "locPrest")
    _sub(loc, "cLocPrestacao", serv["municipio_prestacao_ibge"])
    cs = _sub(s, "cServ")
    _sub(cs, "cTribNac", so_digitos(serv["codigo_tributacao_nacional"]))
    _sub(cs, "xDescServ", nota.descricao)
    if serv.get("codigo_nbs") and not leiaute_novo:
        _sub(cs, "cNBS", so_digitos(serv["codigo_nbs"]))

    v = _sub(inf, "valores")
    vsp = _sub(v, "vServPrest")
    _sub(vsp, "vServ", formatar_decimal(nota.valor))
    trib = _sub(v, "trib")
    tm = _sub(trib, "tribMun")
    _sub(tm, "tribISSQN", "1")   # 1 = Operação tributável
    _sub(tm, "tpRetISSQN", "1")  # 1 = Não retido
    tot = _sub(trib, "totTrib")
    _sub(tot, "pTotTribSN", formatar_decimal(nota.aliquota_simples))

    return raiz


NS_DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"


def _ds(pai, tag, texto=None, **attrs):
    el = etree.SubElement(pai, f"{{{NS_DS}}}{tag}", attrs)
    if texto is not None:
        el.text = texto
    return el


def _c14n(el: etree._Element) -> bytes:
    """Canonical XML 1.0 (inclusivo) de um elemento.

    O C14N inclusivo do lxml/libxml2 emite `xmlns=""` indevidos em subárvores de documentos
    com namespace padrão. Como aqui o único namespace em escopo é o padrão (usado pelo próprio
    elemento), o C14N exclusivo produz exatamente os mesmos bytes que o inclusivo pela
    especificação — então usamos ele, com uma checagem para garantir essa condição.
    """
    if set(el.nsmap) != {None}:
        raise ValueError(f"Namespaces inesperados em {el.tag}: {el.nsmap}")
    return etree.tostring(el, method="c14n", exclusive=True)


def assinar_dps(raiz: etree._Element, cert: CertificadoA1) -> etree._Element:
    """Assinatura XMLDSig envelopada (RSA-SHA256, C14N inclusiva) sobre o infDPS.

    A <Signature> fica como filha de <DPS>, logo após <infDPS>, no namespace padrão
    (sem prefixo "ds:"), como exige o leiaute da NFS-e Nacional.
    """
    inf = raiz.find(f"{{{NS_NFSE}}}infDPS")
    ref_id = inf.get("Id")
    digest = hashlib.sha256(_c14n(inf)).digest()

    sig = etree.SubElement(raiz, f"{{{NS_DS}}}Signature", nsmap={None: NS_DS})
    si = _ds(sig, "SignedInfo")
    _ds(si, "CanonicalizationMethod", Algorithm=C14N)
    _ds(si, "SignatureMethod", Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
    ref = _ds(si, "Reference", URI="#" + ref_id)
    tr = _ds(ref, "Transforms")
    _ds(tr, "Transform", Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature")
    _ds(tr, "Transform", Algorithm=C14N)
    _ds(ref, "DigestMethod", Algorithm="http://www.w3.org/2001/04/xmlenc#sha256")
    _ds(ref, "DigestValue", base64.b64encode(digest).decode())

    chave = serialization.load_pem_private_key(cert.chave_pem, password=None)
    assinatura = chave.sign(_c14n(si), padding.PKCS1v15(), hashes.SHA256())
    _ds(sig, "SignatureValue", base64.b64encode(assinatura).decode())

    cert_der = x509.load_pem_x509_certificate(cert.cert_pem).public_bytes(serialization.Encoding.DER)
    _ds(_ds(_ds(sig, "KeyInfo"), "X509Data"), "X509Certificate", base64.b64encode(cert_der).decode())
    return raiz


def xml_bytes(raiz: etree._Element) -> bytes:
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")
