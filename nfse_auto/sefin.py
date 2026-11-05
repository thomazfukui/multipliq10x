"""Cliente da API do Sistema Nacional NFS-e (Sefin Nacional + ADN), autenticado por mTLS com o A1."""
from __future__ import annotations

import base64
import gzip
from dataclasses import dataclass, field

import requests

from .certificado import CertificadoA1

URLS = {
    "producao": {
        "sefin": "https://sefin.nfse.gov.br/SefinNacional",
        "adn": "https://adn.nfse.gov.br",
    },
    "homologacao": {
        "sefin": "https://sefin.producaorestrita.nfse.gov.br/SefinNacional",
        "adn": "https://adn.producaorestrita.nfse.gov.br",
    },
}


class ErroSefin(Exception):
    def __init__(self, mensagem: str, status: int | None = None, erros: list | None = None):
        super().__init__(mensagem)
        self.status = status
        self.erros = erros or []


@dataclass
class NfseAutorizada:
    chave_acesso: str
    numero_nfse: str
    xml: bytes
    alertas: list = field(default_factory=list)


def _gzip_b64(dados: bytes) -> str:
    return base64.b64encode(gzip.compress(dados)).decode()


def _desgzip_b64(texto: str) -> bytes:
    return gzip.decompress(base64.b64decode(texto))


def _numero_nfse(xml: bytes) -> str:
    from lxml import etree

    raiz = etree.fromstring(xml)
    achado = raiz.find(".//{http://www.sped.fazenda.gov.br/nfse}nNFSe")
    return achado.text if achado is not None else ""


def _erros(resp: requests.Response) -> list:
    try:
        corpo = resp.json()
    except ValueError:
        return [{"Descricao": resp.text[:500]}]
    return corpo.get("erros") or corpo.get("Erros") or [corpo]


class ClienteSefin:
    def __init__(self, ambiente: str, cert: CertificadoA1, timeout: int = 60):
        self.urls = URLS[ambiente]
        self.cert = cert
        self.timeout = timeout

    def _req(self, metodo: str, url: str, **kw) -> requests.Response:
        with self.cert.arquivos_mtls() as par:
            return requests.request(metodo, url, cert=par, timeout=self.timeout, **kw)

    def emitir(self, dps_assinada: bytes) -> NfseAutorizada:
        resp = self._req(
            "POST",
            f"{self.urls['sefin']}/nfse",
            json={"dpsXmlGZipB64": _gzip_b64(dps_assinada)},
            headers={"Accept": "application/json"},
        )
        if resp.status_code not in (200, 201):
            erros = _erros(resp)
            raise ErroSefin(f"Emissão recusada (HTTP {resp.status_code}): {erros}", resp.status_code, erros)
        corpo = resp.json()
        xml = _desgzip_b64(corpo["nfseXmlGZipB64"])
        return NfseAutorizada(
            chave_acesso=corpo["chaveAcesso"],
            numero_nfse=_numero_nfse(xml),
            xml=xml,
            alertas=corpo.get("alertas") or [],
        )

    def chave_por_id_dps(self, id_dps: str) -> str | None:
        """Se a DPS já virou NFS-e (ex.: falha de rede após o envio), devolve a chave de acesso."""
        resp = self._req("GET", f"{self.urls['sefin']}/dps/{id_dps}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ErroSefin(f"Consulta da DPS falhou (HTTP {resp.status_code})", resp.status_code, _erros(resp))
        return resp.json().get("chaveAcesso")

    def baixar_nfse(self, chave: str) -> NfseAutorizada:
        resp = self._req("GET", f"{self.urls['sefin']}/nfse/{chave}")
        if resp.status_code != 200:
            raise ErroSefin(f"Consulta da NFS-e falhou (HTTP {resp.status_code})", resp.status_code, _erros(resp))
        xml = _desgzip_b64(resp.json()["nfseXmlGZipB64"])
        return NfseAutorizada(chave_acesso=chave, numero_nfse=_numero_nfse(xml), xml=xml)

    def baixar_danfse(self, chave: str) -> bytes:
        resp = self._req("GET", f"{self.urls['adn']}/danfse/{chave}", headers={"Accept": "application/pdf"})
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            raise ErroSefin(f"Download do DANFSe falhou (HTTP {resp.status_code})", resp.status_code)
        return resp.content
