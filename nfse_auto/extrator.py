"""Lê o relatório de comissão (PDF) com o Claude e devolve valor e período."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
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

- o valor TOTAL da comissão que deve ser faturado na nota (não some estornos nem itens
  marcados como já pagos, a menos que o relatório indique que o total a faturar os inclui);
- a data final do período do relatório (a nota cita "Referente ao período: ... até <data final>").
  O assunto do e-mail costuma trazer essa data, ex.: "(até 22/10/2026)"; se o PDF mostrar
  outra data, use a do PDF, marque confiança "media" e explique;
- a data inicial, se existir;
- quaisquer instruções de faturamento que apareçam no PDF ou no corpo do e-mail.

Se houver mais de um total plausível, ou se o valor a faturar não estiver explícito,
marque confiança "media" ou "baixa" e explique na justificativa. Não invente valores."""


class ErroExtracao(Exception):
    pass


@dataclass
class DadosRelatorio:
    valor: Decimal
    periodo_inicio: date | None
    periodo_fim: date
    cnpj_tomador: str
    instrucoes: str
    confianca: str
    justificativa: str


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
