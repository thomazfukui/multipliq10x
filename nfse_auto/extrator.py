"""Lê o relatório de comissão (PDF) com o Claude e devolve valor e período."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import anthropic

MODELO = "claude-opus-5-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "valor_comissao": {
            "type": "string",
            "description": "Valor total da comissão a faturar, só números com ponto decimal, ex.: 1234.56. Vazio se não encontrar.",
        },
        "periodo_inicio": {
            "type": "string",
            "description": "Data inicial do período do relatório, formato AAAA-MM-DD. Vazio se não houver.",
        },
        "periodo_fim": {
            "type": "string",
            "description": "Data final do período do relatório (até quando as comissões foram apuradas), formato AAAA-MM-DD.",
        },
        "cnpj_tomador": {
            "type": "string",
            "description": "CNPJ de quem paga a comissão, se aparecer. Vazio se não houver.",
        },
        "instrucoes_faturamento": {
            "type": "string",
            "description": "Instruções de faturamento que aparecem no e-mail ou no PDF, resumidas. Vazio se não houver.",
        },
        "confianca": {
            "type": "string",
            "enum": ["alta", "media", "baixa"],
            "description": "alta = valor e data final estão explícitos e sem ambiguidade.",
        },
        "justificativa": {
            "type": "string",
            "description": "De onde saíram o valor e a data (rótulo/linha do relatório) e qualquer ambiguidade.",
        },
    },
    "required": [
        "valor_comissao",
        "periodo_inicio",
        "periodo_fim",
        "cnpj_tomador",
        "instrucoes_faturamento",
        "confianca",
        "justificativa",
    ],
    "additionalProperties": False,
}

INSTRUCOES = """Você recebe um relatório de comissão em PDF enviado por e-mail a um representante
comercial, que precisa emitir uma NFS-e com base nele. Extraia:

- o valor TOTAL da comissão que deve ser faturado na nota. No "FECHAMENTO DE COMISSÃO" é o
  "Valor Líquido" do cabeçalho; se Débitos, IRRF ou ISS forem diferentes de zero, use o líquido
  mas marque confiança "media" e explique;
- a data final do período do relatório (a nota cita "Referente ao período: ... até <data final>").
  O assunto do e-mail costuma trazer essa data, ex.: "(até 22/10/2026)"; se o PDF mostrar
  outra data, use a do PDF, marque confiança "media" e explique;
- a data inicial, se existir;
- quaisquer instruções de faturamento que apareçam no PDF ou no corpo do e-mail.

Se houver mais de um total plausível, ou se o valor a faturar não estiver explícito,
marque confiança "media" ou "baixa" e explique na justificativa. Não invente valores."""


class ErroExtracao(Exception):
    pass


# --------------------------------------------------------------------------- leitor exato (formato Roma)
_NUM = r"\d{1,3}(?:\.\d{3})*,\d{2}"


def _dec(br: str) -> Decimal:
    return Decimal(br.replace(".", "").replace(",", "."))


def ler_texto_roma(texto: str, cnpj_prestador: str) -> "DadosRelatorio | None":
    """Lê o cabeçalho do "FECHAMENTO DE COMISSÃO" da Roma, sem IA.

    Cabeçalho (texto extraído do PDF):
        BENEFICIÁRIO : L.D.T. COMUNICACAO VISUAL LTDA Débitos Créditos IRRF ISS Valor Líquido
        0,00 14.162,26 0,00 0,00 14.162,2610947101000128
        Período de : 01/08/2025 até 22/10/2026
        ...
        Total Geral 218 26.794.998,93 14.162,26

    Devolve None se o texto não estiver nesse formato (aí o Claude é usado).
    """
    m_cab = re.search(r"BENEFICI.RIO\s*:.*?L.quido\s*\n([^\n]*)", texto)
    m_per = re.search(r"Per.odo de\s*:\s*(\d{2}/\d{2}/\d{4})\s*at.\s*(\d{2}/\d{2}/\d{4})", texto)
    if not (m_cab and m_per):
        return None
    linha = m_cab.group(1)
    valores = re.findall(_NUM, linha)
    if len(valores) < 5:
        return None
    debitos, creditos, irrf, iss, liquido = (_dec(v) for v in valores[:5])
    alertas: list[str] = []
    if cnpj_prestador and cnpj_prestador not in re.sub(r"\D", "", linha):
        alertas.append("O CNPJ do beneficiário no relatório não é o da sua empresa.")
    if liquido != creditos - debitos - irrf - iss:
        alertas.append("Valor líquido não fecha com créditos − débitos − IRRF − ISS.")
    if debitos or irrf or iss:
        alertas.append(
            f"Relatório com débitos R$ {valores[0]}, IRRF R$ {valores[2]} ou ISS R$ {valores[3]}: "
            "confira se a nota deve sair pelo líquido."
        )
    m_tot = re.search(rf"Total Geral\s+\d+\s+{_NUM}\s+({_NUM})", texto)
    if m_tot and _dec(m_tot.group(1)) != creditos:
        alertas.append(f"Total Geral (R$ {m_tot.group(1)}) difere dos créditos do cabeçalho (R$ {valores[1]}).")
    return DadosRelatorio(
        valor=liquido,
        periodo_inicio=_data(m_per.group(1)),
        periodo_fim=_data(m_per.group(2)),
        cnpj_tomador="",
        instrucoes="",
        confianca="alta" if not alertas else "media",
        justificativa=(
            f"Leitor exato do formato Roma: Valor Líquido do cabeçalho (créditos R$ {valores[1]}, "
            f"débitos R$ {valores[0]}, IRRF R$ {valores[2]}, ISS R$ {valores[3]})"
            + (f"; Total Geral R$ {m_tot.group(1)}" if m_tot else "")
            + "."
        ),
        alertas=alertas,
        leitor="roma",
    )


def ler_pdf_roma(pdf: bytes, cnpj_prestador: str) -> "DadosRelatorio | None":
    try:
        from pypdf import PdfReader
        import io

        leitor = PdfReader(io.BytesIO(pdf))
        texto = "\n".join(p.extract_text() or "" for p in leitor.pages)
    except Exception:  # noqa: BLE001 — PDF estranho: deixa para o Claude
        return None
    return ler_texto_roma(texto, cnpj_prestador)


@dataclass
class DadosRelatorio:
    valor: Decimal
    periodo_inicio: date | None
    periodo_fim: date
    cnpj_tomador: str
    instrucoes: str
    confianca: str
    justificativa: str
    alertas: list = field(default_factory=list)
    leitor: str = "claude"


def _data(texto: str) -> date | None:
    texto = (texto or "").strip()
    if not texto:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    raise ErroExtracao(f"Data em formato inesperado: {texto!r}")


def interpretar(bruto: dict) -> DadosRelatorio:
    try:
        valor = Decimal(bruto["valor_comissao"].replace(",", ".")) if bruto["valor_comissao"] else None
    except InvalidOperation as e:
        raise ErroExtracao(f"Valor inválido: {bruto['valor_comissao']!r}") from e
    fim = _data(bruto["periodo_fim"])
    if valor is None or fim is None:
        raise ErroExtracao(f"Relatório sem valor ou data final. Detalhe: {bruto.get('justificativa')}")
    return DadosRelatorio(
        valor=valor,
        periodo_inicio=_data(bruto["periodo_inicio"]),
        periodo_fim=fim,
        cnpj_tomador=bruto["cnpj_tomador"],
        instrucoes=bruto["instrucoes_faturamento"],
        confianca=bruto["confianca"],
        justificativa=bruto["justificativa"],
    )


def extrair(
    pdfs: list[bytes],
    corpo_email: str = "",
    assunto: str = "",
    cliente: anthropic.Anthropic | None = None,
) -> DadosRelatorio:
    cliente = cliente or anthropic.Anthropic()
    conteudo: list[dict] = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(pdf).decode(),
            },
        }
        for pdf in pdfs
    ]
    conteudo.append(
        {
            "type": "text",
            "text": INSTRUCOES + f"\n\nAssunto do e-mail: {assunto or '(vazio)'}"
            + "\n\nCorpo do e-mail que trouxe o PDF:\n<email>\n"
            + (corpo_email or "(vazio)")[:8000] + "\n</email>",
        }
    )
    resp = cliente.messages.create(
        model=MODELO,
        max_tokens=16000,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": conteudo}],
        # Se o modelo recusar por engano, a API refaz a chamada em outro modelo automaticamente.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if resp.stop_reason == "refusal":
        raise ErroExtracao("O modelo recusou ler o relatório.")
    if resp.stop_reason == "max_tokens":
        raise ErroExtracao("Resposta do modelo cortada (max_tokens).")
    texto = next((b.text for b in resp.content if b.type == "text"), None)
    if not texto:
        raise ErroExtracao("Resposta do modelo sem texto.")
    return interpretar(json.loads(texto))
