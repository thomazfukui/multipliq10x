"""Fluxo completo: e-mail com relatório → extração → (aprovação) → emissão → envio."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import dps as dps_mod
from . import email_gmail, extrator
from .certificado import CertificadoA1
from .config import DIR_DADOS, Segredos, cliente_por_id
from .estado import Estado
from .sefin import ClienteSefin, ErroSefin

log = logging.getLogger("nfse_auto")


def _br(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _moeda(v: Decimal) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dir_nota(nota_id: int) -> Path:
    p = DIR_DADOS / "notas" / f"{nota_id:05d}"
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- preparação
def preparar_dados(
    cfg: dict,
    cliente: dict,
    rel: extrator.DadosRelatorio,
    hoje: date | None = None,
    data_assunto: date | None = None,
) -> tuple[dict, list[str]]:
    """Monta os dados da nota e lista os motivos que impedem a emissão automática.

    `data_assunto` é a data "(até DD/MM/AAAA)" do assunto do e-mail, usada como conferência.
    """
    hoje = hoje or date.today()
    serv = cliente["servico"]
    motivos: list[str] = []

    inicio = _br(rel.periodo_inicio) if rel.periodo_inicio else serv["periodo_inicio_padrao"]
    fim = _br(rel.periodo_fim)
    descricao = serv["descricao"].format(periodo_inicio=inicio, periodo_fim=fim)

    competencia = hoje
    aliquota = cfg.get("aliquota_simples_por_mes", {}).get(competencia.strftime("%Y-%m"))
    if aliquota is None:
        motivos.append(
            f"Sem alíquota do Simples para {competencia:%m/%Y} em 'aliquota_simples_por_mes' (config.yaml)."
        )

    motivos.extend(rel.alertas)
    if rel.confianca != "alta":
        motivos.append(f"Confiança da leitura do relatório: {rel.confianca}.")
    vmin, vmax = Decimal(str(cliente["valor_minimo"])), Decimal(str(cliente["valor_maximo"]))
    if not (vmin <= rel.valor <= vmax):
        motivos.append(f"Valor R$ {_moeda(rel.valor)} fora do intervalo R$ {_moeda(vmin)} – R$ {_moeda(vmax)}.")
    cnpj_rel = dps_mod.so_digitos(rel.cnpj_tomador)
    if cnpj_rel and cnpj_rel != dps_mod.so_digitos(cliente["tomador"]["cnpj"]):
        motivos.append(f"CNPJ no relatório ({rel.cnpj_tomador}) difere do tomador configurado.")
    if data_assunto and data_assunto != rel.periodo_fim:
        motivos.append(
            f"Data final lida no PDF ({fim}) difere da data do assunto do e-mail ({_br(data_assunto)})."
        )
    if rel.periodo_fim > hoje:
        motivos.append(f"Data final do período ({fim}) está no futuro.")

    dados = {
        "valor": str(rel.valor),
        "periodo_inicio": inicio,
        "periodo_fim": fim,
        "periodo_fim_iso": rel.periodo_fim.isoformat(),
        "descricao": descricao,
        "competencia": competencia.isoformat(),
        "aliquota_simples": str(aliquota) if aliquota is not None else None,
        "extracao": {k: (str(v) if isinstance(v, (Decimal, date)) else v) for k, v in asdict(rel).items()},
    }
    return dados, motivos


def _periodo_repetido(estado: Estado, cliente_id: str, dados: dict) -> str | None:
    ult = estado.ultima_enviada(cliente_id)
    if not ult:
        return None
    anterior = json.loads(ult["dados_json"])
    if dados["periodo_fim_iso"] <= anterior.get("periodo_fim_iso", ""):
        return (
            f"Período até {dados['periodo_fim']} não é posterior ao da última nota enviada "
            f"(NFS-e {ult['numero_nfse']}, até {anterior['periodo_fim']}). Possível duplicidade."
        )
    return None


# --------------------------------------------------------------------------- verificação de e-mails
def verificar_emails(cfg: dict, seg: Segredos, estado: Estado, dias: int = 30) -> list[int]:
    """Procura relatórios novos e cria as notas (pendentes ou já emitidas). Devolve os ids criados."""
    criadas = []
    for cliente in cfg["clientes"]:
        emails = email_gmail.buscar_relatorios(
            seg.gmail_usuario, seg.gmail_senha_app,
            cliente.get("email_remetentes") or [], cliente.get("email_assunto_contem", ""), dias,
            corpo_contem=cliente.get("email_corpo_contem", ""),
            anexo_prefixo=cliente.get("anexo_prefixo", ""),
        )
        for em in emails:
            if not em.message_id or estado.ja_processado(em.message_id):
                continue
            log.info("Novo relatório de %s: %s", em.remetente, em.assunto)
            criadas.append(processar_email(cfg, seg, estado, cliente, em))
    return criadas


class _Campos(dict):
    def __missing__(self, chave):
        return "{" + chave + "}"


def _formatar(modelo: str, dados: dict, **extra) -> str:
    campos = _Campos(
        valor=_moeda(Decimal(dados["valor"])), periodo_inicio=dados["periodo_inicio"],
        periodo_fim=dados["periodo_fim"], **extra,
    )
    return modelo.format_map(campos)


def _email_dict(em: email_gmail.EmailRecebido) -> dict:
    return {
        "message_id": em.message_id, "remetente": em.remetente, "assunto": em.assunto,
        "data": em.data, "references": em.references, "anexos": [n for n, _ in em.pdfs],
    }


def _ler_relatorio(cfg: dict, em: email_gmail.EmailRecebido) -> extrator.DadosRelatorio:
    """Primeiro o leitor exato do formato Roma; se o formato não bater, usa o Claude."""
    cnpj = dps_mod.so_digitos(cfg["prestador"]["cnpj"])
    for _, conteudo in em.pdfs:
        rel = extrator.ler_pdf_roma(conteudo, cnpj)
        if rel:
            return rel
    return extrator.extrair([c for _, c in em.pdfs], em.corpo, em.assunto)


def processar_email(cfg: dict, seg: Segredos, estado: Estado, cliente: dict, em: email_gmail.EmailRecebido) -> int:
    try:
        rel = _ler_relatorio(cfg, em)
    except Exception as e:  # noqa: BLE001 — qualquer falha vira pendência para o responsável
        nota_id = estado.criar(em.message_id, cliente["id"], "erro", {}, _email_dict(em), f"Falha ao ler o relatório: {e}")
        _alertar(cfg, seg, nota_id, f"Não consegui ler o relatório do e-mail \"{em.assunto}\".\n\nErro: {e}")
        return nota_id

    dados, motivos = preparar_dados(
        cfg, cliente, rel, data_assunto=email_gmail.data_ate_no_assunto(em.assunto)
    )
    # Já faturado (ex.: você mandou a nota manualmente)? Então só registra e segue.
    env = cliente["envio"]
    if env.get("pular_se_ja_enviado", True):
        assunto_envio = _formatar(env["assunto"], dados)
        if email_gmail.ja_enviado(seg.gmail_usuario, seg.gmail_senha_app, assunto_envio):
            log.info("Relatório até %s já faturado (achei \"%s\" nos enviados).", dados["periodo_fim"], assunto_envio)
            return estado.criar(em.message_id, cliente["id"], "ja_faturado", dados, _email_dict(em),
                                f"Já existe e-mail enviado: {assunto_envio}")

    nota_id = estado.criar(em.message_id, cliente["id"], "pendente", dados, _email_dict(em))
    for nome, conteudo in em.pdfs:
        (dir_nota(nota_id) / f"relatorio_{nome}").write_bytes(conteudo)
    repetido = _periodo_repetido(estado, cliente["id"], dados)
    if repetido:
        motivos.append(repetido)

    if cfg["modo_aprovacao"] == "automatico" and not motivos:
        emitir_e_enviar(cfg, seg, estado, nota_id)
    else:
        if cfg["modo_aprovacao"] == "manual":
            motivos.insert(0, "Modo de aprovação manual.")
        estado.atualizar(nota_id, motivo=" | ".join(motivos))
        _enviar_previa(cfg, seg, nota_id, cliente, dados, motivos)
    return nota_id


# --------------------------------------------------------------------------- emissão
def emitir_e_enviar(cfg: dict, seg: Segredos, estado: Estado, nota_id: int, enviar_email: bool = True) -> None:
    nota = estado.obter(nota_id)
    if nota is None:
        raise ValueError(f"Nota {nota_id} não existe.")
    if nota["status"] == "enviada":
        raise ValueError(f"Nota {nota_id} já foi emitida e enviada (NFS-e {nota['numero_nfse']}).")
    cliente = cliente_por_id(cfg, nota["cliente_id"])
    dados = json.loads(nota["dados_json"])
    if not dados.get("valor"):
        raise ValueError(f"Nota {nota_id} não tem dados extraídos (status {nota['status']}).")
    if dados.get("aliquota_simples") is None:
        mes = dados["competencia"][:7]
        aliq = cfg.get("aliquota_simples_por_mes", {}).get(mes)
        if aliq is None:
            raise ValueError(f"Cadastre a alíquota do Simples de {mes} em 'aliquota_simples_por_mes'.")
        dados["aliquota_simples"] = str(aliq)
        estado.atualizar(nota_id, dados=dados)

    cert = CertificadoA1.de_pfx(seg.cert_pfx, seg.cert_senha)
    cert.verificar_validade()
    api = ClienteSefin(cfg["ambiente"], cert)
    pasta = dir_nota(nota_id)

    ja_emitida = nota["status"] == "emitida"
    try:
        if not ja_emitida:
            autorizada = _emitir(cfg, estado, api, nota, cliente, dados, cert, pasta)
        else:
            autorizada = api.baixar_nfse(nota["chave_acesso"])
        (pasta / f"nfse_{autorizada.chave_acesso}.xml").write_bytes(autorizada.xml)
        estado.atualizar(nota_id, status="emitida", chave_acesso=autorizada.chave_acesso,
                         numero_nfse=autorizada.numero_nfse, motivo="")
        ja_emitida = True
        log.info("NFS-e %s autorizada (chave %s)", autorizada.numero_nfse, autorizada.chave_acesso)
        danfse = api.baixar_danfse(autorizada.chave_acesso)
        (pasta / f"danfse_{autorizada.chave_acesso}.pdf").write_bytes(danfse)
    except Exception as e:
        estado.atualizar(nota_id, status="emitida" if ja_emitida else "erro", motivo=str(e))
        _alertar(cfg, seg, nota_id, f"Falha ao emitir/baixar a NFS-e da nota {nota_id}.\n\nErro: {e}")
        raise

    if enviar_email:
        enviar_nota(cfg, seg, estado, nota_id, autorizada, danfse)


def _emitir(cfg, estado, api, nota, cliente, dados, cert, pasta):
    # Idempotência: se já reservamos um nDPS e a DPS chegou a virar nota, apenas recupera.
    if nota["id_dps"]:
        chave = api.chave_por_id_dps(nota["id_dps"])
        if chave:
            log.info("DPS %s já tinha sido autorizada; recuperando a NFS-e.", nota["id_dps"])
            return api.baixar_nfse(chave)

    numero = estado.proximo_numero_dps(cfg["dps"]["serie"], int(cfg["dps"]["numero_inicial"]))
    ident = dps_mod.id_dps(cfg["prestador"]["municipio_ibge"], cfg["prestador"]["cnpj"], cfg["dps"]["serie"], numero)
    estado.atualizar(nota["id"], numero_dps=numero, id_dps=ident)

    xml = dps_mod.montar_dps(cfg, cliente, dps_mod.DadosNota(
        numero_dps=numero,
        valor=Decimal(dados["valor"]),
        descricao=dados["descricao"],
        competencia=date.fromisoformat(dados["competencia"]),
        aliquota_simples=Decimal(dados["aliquota_simples"]),
    ))
    assinado = dps_mod.xml_bytes(dps_mod.assinar_dps(xml, cert))
    (pasta / f"dps_{numero}.xml").write_bytes(assinado)
    try:
        return api.emitir(assinado)
    except ErroSefin as e:
        (pasta / f"erro_dps_{numero}.json").write_text(json.dumps(e.erros, ensure_ascii=False, indent=2))
        raise


def enviar_nota(cfg, seg, estado, nota_id, autorizada, danfse: bytes) -> None:
    nota = estado.obter(nota_id)
    cliente = cliente_por_id(cfg, nota["cliente_id"])
    dados = json.loads(nota["dados_json"])
    env = cliente["envio"]
    extra = {"numero_nfse": autorizada.numero_nfse, "chave_acesso": autorizada.chave_acesso}
    assunto = _formatar(env["assunto"], dados, **extra)
    corpo = _formatar(env["corpo"], dados, **extra)
    corpo_html = _formatar(env["corpo_html"], dados, **extra) if env.get("corpo_html") else None

    anexos = [(f"{autorizada.chave_acesso}.pdf", danfse, "application/pdf")]
    if env.get("anexar_xml", False):
        anexos.append((f"{autorizada.chave_acesso}.xml", autorizada.xml, "application/xml"))
    if env.get("anexar_relatorio", True):
        for arq in sorted(dir_nota(nota_id).glob("relatorio_*")):
            anexos.append((arq.name.removeprefix("relatorio_"), arq.read_bytes(), "application/pdf"))

    em = json.loads(nota["email_json"])
    original = None
    para = list(env["para"])
    if env.get("responder_no_fio") and em.get("message_id"):
        original = email_gmail.EmailRecebido(
            message_id=em["message_id"], remetente=em["remetente"], assunto=em["assunto"],
            data=em["data"], corpo="", references=em.get("references", ""),
        )
    cc = list(env.get("cc") or [])
    if cfg["ambiente"] == "homologacao":
        # Em teste, nunca manda para o cliente: só para você.
        assunto = "[HOMOLOGAÇÃO - SEM VALOR FISCAL] " + assunto
        para, cc, original = [cfg["email_responsavel"]], [], None

    email_gmail.enviar(
        seg.gmail_usuario, seg.gmail_senha_app, para, assunto, corpo,
        anexos=anexos, cc=cc, em_resposta_a=original, corpo_html=corpo_html,
        confirmacao_leitura=env.get("confirmacao_leitura", False),
    )
    estado.atualizar(nota_id, status="enviada")
    log.info("NFS-e %s enviada para %s", autorizada.numero_nfse, ", ".join(para + cc))


# --------------------------------------------------------------------------- avisos ao responsável
def _enviar_previa(cfg, seg, nota_id, cliente, dados, motivos) -> None:
    corpo = (
        f"Chegou um relatório de comissão de {cliente['nome']} e a nota abaixo está pronta para emitir.\n\n"
        f"Por que precisa da sua aprovação:\n- " + "\n- ".join(motivos) + "\n\n"
        f"Tomador: {cliente['tomador']['razao_social']} (CNPJ {cliente['tomador']['cnpj']})\n"
        f"Valor: R$ {_moeda(Decimal(dados['valor']))}\n"
        f"Competência: {dados['competencia']}\n"
        f"Alíquota Simples: {dados['aliquota_simples'] or 'NÃO CADASTRADA'}%\n"
        f"Ambiente: {cfg['ambiente']}\n\n"
        f"Descrição:\n{dados['descricao']}\n\n"
        f"Como o valor foi lido: {dados['extracao']['justificativa']}\n\n"
        f"Para emitir e enviar, rode:\n    python -m nfse_auto aprovar {nota_id}\n"
        f"Para descartar:\n    python -m nfse_auto descartar {nota_id}\n"
    )
    _enviar_ao_responsavel(cfg, seg, f"[NFS-e] Aprovar nota #{nota_id} - {cliente['nome']} R$ {_moeda(Decimal(dados['valor']))}", corpo)


def _alertar(cfg, seg, nota_id, texto) -> None:
    _enviar_ao_responsavel(cfg, seg, f"[NFS-e] ERRO na nota #{nota_id}", texto)


def _enviar_ao_responsavel(cfg, seg, assunto, corpo) -> None:
    log.warning("%s\n%s", assunto, corpo)
    try:
        email_gmail.enviar(seg.gmail_usuario, seg.gmail_senha_app, [cfg["email_responsavel"]], assunto, corpo)
    except Exception:  # noqa: BLE001 — o aviso por e-mail não pode derrubar o fluxo
        log.exception("Não consegui enviar o aviso por e-mail.")
