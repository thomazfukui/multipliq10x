# Instalação no Mac — passo a passo

Leva uns 20 minutos. Os comandos vão no app **Terminal** (`⌘ + espaço`, digite "Terminal"). Copie e cole um bloco por vez.

---

## 1. Ferramentas básicas (só na primeira vez)

**Git e ferramentas da Apple.** Se aparecer uma janela pedindo para instalar, clique em *Instalar*:

```bash
xcode-select --install
```

**Homebrew e Python 3.12:**

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

No fim, o Homebrew mostra dois comandos em *Next steps* (começam com `echo` e `eval`). Rode os dois. Depois instale o Python:

```bash
brew install python@3.12 gh
```

## 2. Baixar o projeto

O repositório é privado, então primeiro faça login no GitHub. Escolha *GitHub.com → HTTPS → Login with a web browser*:

```bash
gh auth login
```

Depois baixe o projeto para a sua pasta pessoal:

```bash
gh repo clone thomazfukui/multipliq10x ~/multipliq10x -- -b claude/inspiring-faraday-mw5z02
cd ~/multipliq10x
```

> Use a pasta pessoal (`~/multipliq10x`), e **não** Documentos, Mesa ou iCloud. O macOS bloqueia programas agendados nessas pastas.

## 3. Instalar

```bash
./scripts/instalar_mac.sh
```

Esse script cria o ambiente Python e os arquivos `config.yaml` (ajustes) e `.env` (senhas). Nenhum dos dois vai para o git.

## 4. Certificado digital

Copie o arquivo `.pfx` para a pasta `segredos/` com o nome `certificado.pfx`. Pelo Finder: `⌘ + shift + G`, digite `~/multipliq10x/segredos` e arraste o arquivo para lá, renomeando. Ou pelo Terminal, trocando o caminho de origem pelo seu:

```bash
cp ~/Downloads/NOME-DO-SEU-ARQUIVO.pfx segredos/certificado.pfx
```

## 5. Senha de app do Gmail

1. Ative a verificação em duas etapas: <https://myaccount.google.com/signinoptions/twosv>
2. Crie a senha de app: <https://myaccount.google.com/apppasswords>. Dê o nome "NFS-e" e copie os 16 caracteres.

## 6. Preencher o `.env`

```bash
open -e .env
```

Preencha as linhas e salve (`⌘ + S`). A senha de app vai **sem espaços**:

```
NFSE_CERT_SENHA=senha-do-certificado
GMAIL_USUARIO=seuemail@gmail.com
GMAIL_SENHA_APP=senha-de-app-de-16-letras
```

Confira o certificado:

```bash
.venv/bin/python -m nfse_auto testar-certificado
```

O resultado esperado mostra `Titular: CN=L D T COMUNICACAO VISUAL LTDA:10947101000128 …` e `Validade: 16/12/2026`.

---

## 7. Primeira nota de TESTE (homologação, sem valor fiscal)

O `config.yaml` começa em `ambiente: homologacao`. Nesse modo, as notas vão para a *Produção Restrita* da Receita e os e-mails chegam **só para você**.

```bash
.venv/bin/python -m nfse_auto verificar
```

O que deve acontecer:

- O relatório **até 22/10/2026** aparece como `ja_faturado`, porque você já mandou essa nota (a nº 27).
- O relatório **até 29/10/2026** (R$ 2.986,09) aparece como `pendente`, e você recebe o e-mail **"[NFS-e] Aprovar nota #N"**.

Emita a nota de teste trocando `N` pelo número do e-mail:

```bash
.venv/bin/python -m nfse_auto aprovar N
```

Você recebe o e-mail **"[HOMOLOGAÇÃO - SEM VALOR FISCAL] RELATÓRIO DE COMISSÃO <> … (até 29/10/2026)"**, com o PDF da nota de teste e o relatório. Os arquivos ficam em `dados/homologacao/notas/`.

**Se der erro:** a mensagem aparece no Terminal e o detalhe da Receita fica em `dados/homologacao/notas/0000N/erro_dps_*.json`. Esse arquivo não tem senha nem dado sigiloso, então pode me mandar o conteúdo que eu ajusto.

## 8. Primeira nota REAL

Abra o `config.yaml` (`open -e config.yaml`) e troque `ambiente: homologacao` por `ambiente: producao`. Depois rode:

```bash
.venv/bin/python -m nfse_auto verificar
```

Você recebe o e-mail "Aprovar nota #N" com os dados da nota. Confira e emita:

```bash
.venv/bin/python -m nfse_auto aprovar N
```

A nota é emitida e o e-mail vai para comissao@consorcioroma.com.br, no seu padrão de sempre. Confira no portal <https://www.nfse.gov.br/EmissorNacional> e no seu Gmail, em *Enviados*.

> O teste e a produção têm registros separados (`dados/homologacao/` e `dados/producao/`), então o teste não impede a nota real.

## 9. Ligar o automático

Quando estiver tudo certo, faça dois ajustes:

1. No `config.yaml`, troque `modo_aprovacao: manual` por `modo_aprovacao: automatico`.
2. Agende a verificação de hora em hora:

   ```bash
   ./scripts/agendar_mac.sh
   ```

A partir daí, quando o relatório da Roma chegar, a nota sai sozinha. Ela volta para a sua aprovação por e-mail se houver débito, IRRF ou ISS, se os valores não fecharem, se o período for repetido ou se faltar a alíquota do mês.

- O Mac precisa estar **ligado e com a sessão aberta**. Em *Ajustes → Bateria/Energia*, evite que ele durma por completo.
- Log: `dados/log.txt`. Para desligar: `./scripts/agendar_mac.sh desligar`.

---

## Rotina

| Quando | O quê |
|---|---|
| Todo mês | Atualizar `aliquota_simples_por_mes` no `config.yaml` (ex.: `"2026-12": 2.05`). Sem isso, nada é emitido e você recebe um aviso. |
| Antes de 16/12/2026 | **Renovar o certificado A1** e trocar o arquivo `segredos/certificado.pfx` (e a senha no `.env`). |
| Quando eu mandar melhorias | `cd ~/multipliq10x && git pull` |

## Comandos úteis

```bash
.venv/bin/python -m nfse_auto status            # lista as notas
.venv/bin/python -m nfse_auto status pendente   # só as que aguardam aprovação
.venv/bin/python -m nfse_auto descartar N       # descarta uma pendente
.venv/bin/python -m nfse_auto ler-relatorio arquivo.pdf --cliente consorcio_roma   # testa um PDF
```

## Problemas comuns

| Mensagem | Solução |
|---|---|
| `Username and Password not accepted` | Use a **senha de app**, sem espaços, e não a senha normal do Google. |
| `Mac verify error` / senha do certificado | Confira `NFSE_CERT_SENHA` no `.env`. |
| `Operation not permitted` no agendamento | O projeto está em Documentos ou Mesa: mova para `~/multipliq10x`. |
| `Sem alíquota do Simples para MM/AAAA` | Cadastre a alíquota do mês no `config.yaml`. |
