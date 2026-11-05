# multipliq10x — emissão automática de NFS-e

Automatiza o ciclo de faturamento da **L.D.T. Comunicação Visual Ltda** (CNPJ 10.947.101/0001-28, Londrina/PR, Simples Nacional):

```
E-mail com relatório de comissão (PDF)
        │  Gmail (IMAP)
        ▼
Leitura do PDF com o Claude → valor + data final do período
        │  checagens de segurança
        ▼
Aprovação (manual no início, automática depois)
        │
        ▼
DPS montada e assinada com o certificado A1 → API do Emissor Nacional (nfse.gov.br)
        │
        ▼
NFS-e autorizada: XML + DANFSe (PDF) → e-mail ao cliente, respondendo no mesmo fio
```

Hoje o projeto está configurado para o **Consórcio Roma** (tomador: Cooperativa Mista Roma, CNPJ 61.550.836/0001-54), com os mesmos campos que você preenche no portal:

| Campo do portal | Valor usado |
|---|---|
| Emitente | Prestador |
| Regime | Simples Nacional, ME/EPP, apuração federal e municipal pelo SN |
| Local da prestação | Londrina/PR (IBGE 4113700) |
| Código de Tributação Nacional | 17.06.01 |
| NBS | 114061900 |
| ISSQN | Operação tributável, sem retenção |
| Valor aproximado dos tributos | Alíquota do Simples (2,01% em 11/2026) |
| IBS/CBS | Não preenchido |
| Descrição | Texto padrão com "Referente ao período: 01/08/2025 até **<data do relatório>**" e os dados bancários |

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
```

Preencha o `.env` com três credenciais:

1. **Certificado A1 em arquivo `.pfx`**. A API exige o arquivo do certificado; não basta ele estar instalado no navegador. Como exportar:
   - **macOS**: abra *Acesso às Chaves*, procure o certificado da empresa, clique com o botão direito em *Exportar*, escolha o formato `.p12` e defina uma senha.
   - **Windows**: rode `certmgr.msc`, vá em *Pessoal → Certificados*, clique com o botão direito no certificado, escolha *Todas as tarefas → Exportar* e marque *Sim, exportar a chave privada* (formato `.pfx`).
   - Se você ainda tiver o `.pfx` original enviado pela certificadora, pode usar ele direto.
2. **Senha de app do Gmail**: crie em <https://myaccount.google.com/apppasswords>. É preciso ter a verificação em duas etapas ativa.
3. **Chave da API da Anthropic**: crie em <https://console.anthropic.com>. É usada só para ler o PDF do relatório (custa alguns centavos por relatório).

Depois confira o certificado:

```bash
python -m nfse_auto testar-certificado
```

## Como usar

| Comando | O que faz |
|---|---|
| `python -m nfse_auto verificar` | Procura relatórios novos no Gmail (últimos 30 dias) e processa cada um |
| `python -m nfse_auto status [pendente\|enviada\|erro]` | Lista as notas |
| `python -m nfse_auto aprovar <id>` | Emite e envia uma nota pendente |
| `python -m nfse_auto descartar <id>` | Descarta uma nota pendente |
| `python -m nfse_auto ler-relatorio arquivo.pdf --cliente consorcio_roma` | Testa a leitura de um PDF, sem emitir nada |
| `python -m nfse_auto gerar-dps --cliente consorcio_roma --valor 100 --fim 2026-10-31` | Gera e imprime o XML assinado, sem enviar |

### Plano de validação (do teste até o automático)

1. **Homologação**: `ambiente: homologacao` (padrão) usa a *Produção Restrita* do Emissor Nacional. As notas emitidas lá não têm valor fiscal, e o e-mail com a nota vai **só para você**, nunca para o cliente.
   - Rode `ler-relatorio` com um relatório real e confira o valor e a data lidos.
   - Rode `verificar`. Você recebe um e-mail "Aprovar nota #N". Depois rode `aprovar N` e confira o XML e o PDF que chegarem.
2. **Primeira nota real**: troque para `ambiente: producao` e mantenha `modo_aprovacao: manual`. Aprove a nota com `aprovar N` e confira no portal nfse.gov.br.
3. **Automático**: troque para `modo_aprovacao: automatico`. A partir daí, a nota é emitida e enviada sozinha, **exceto** em cinco situações, que voltam para a sua aprovação por e-mail:
   - a leitura do relatório não teve confiança "alta";
   - o valor está fora do intervalo `valor_minimo`–`valor_maximo` do cliente;
   - o CNPJ do relatório é diferente do tomador cadastrado;
   - a data final do período não é posterior à da última nota enviada (proteção contra duplicidade);
   - falta a alíquota do Simples do mês em `aliquota_simples_por_mes`.

### Todo mês

Atualize a alíquota do Simples no `config.yaml`:

```yaml
aliquota_simples_por_mes:
  "2026-11": 2.01
  "2026-12": 2.05   # exemplo
```

Se o mês não estiver cadastrado, nada é emitido e você recebe um aviso.

### Agendamento

Para rodar a cada hora em um computador ou servidor que fique ligado:

```bash
crontab -e
# adicionar a linha:
0 * * * * cd /caminho/multipliq10x && .venv/bin/python -m nfse_auto verificar >> dados/log.txt 2>&1
```

## Onde ficam os dados

Os arquivos ficam em `dados/`, que está fora do git:

- `estado.db`: e-mails já processados, status de cada nota e numeração da DPS. Um mesmo e-mail nunca gera duas notas, e um número de DPS nunca é reutilizado.
- `notas/00001/`: o relatório recebido, a DPS assinada, a NFS-e (XML), o DANFSe (PDF) e, se houver, o erro devolvido pela Receita.

Se a conexão cair depois do envio da DPS, a próxima tentativa consulta a DPS pelo identificador antes de reenviar, para não emitir a nota em duplicidade.

## Pontos a confirmar na homologação

Estes itens seguem o leiaute nacional, mas só a primeira emissão em homologação confirma que estão certos. Se a Receita recusar, o motivo fica em `dados/notas/<id>/erro_dps_*.json` e quase sempre se resolve só com uma mudança no `config.yaml`:

- **Versão do leiaute** (`dps.versao_layout`, hoje `1.00`). Com a reforma tributária, a Receita pode exigir uma versão mais nova.
- **Série da DPS** (`dps.serie`). A numeração da automação é independente da usada pelo portal web.
- **Tributação federal (PIS/COFINS)**. O portal marca "00 – Nenhum / Não retidos". A automação não envia esse grupo, que é opcional no leiaute.
- **Quebras de linha na descrição**. Vão como estão no texto do `config.yaml`.

## Testes

```bash
pytest -q
```

Os testes cobrem a montagem da DPS, a assinatura (conferida com o libxmlsec1), as regras de bloqueio, a numeração e o fluxo completo, com a API e o e-mail simulados.
