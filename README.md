# mcp-health-server

Um servidor **Model Context Protocol (MCP)** compatível com Claude que expõe ferramentas
de um domínio de saúde sobre **dados 100% sintéticos**, construído com o rigor que um
domínio regulado exige: validação estrita no servidor, log de auditoria com PII redigida,
autorização OAuth 2.1 com escopos por ferramenta, tracing sem PII e uma **suíte de
red-team que prova, no CI, que os guardrails seguram**. A ação de escrita é marcada como
consequente, então o host pede confirmação ao usuário.

Qualquer host MCP — Claude Desktop, Cursor, VS Code, o MCP Inspector — conecta por stdio
(local) ou Streamable HTTP (remoto).

## O que isto faz, na prática

Um modelo de IA, sozinho, consegue *falar* sobre um paciente, mas não consegue *consultá-lo*,
*verificar uma consulta* nem *marcar uma* — ele não tem mãos. **O MCP é o padrão que dá
mãos a ele:** um servidor publica um conjunto de ações tipadas ("tools"), e qualquer
assistente compatível com MCP (Claude Desktop, Cursor, VS Code…) consegue descobrir e
chamar essas ações no meio de uma conversa.

Este projeto é esse servidor, para uma pequena fatia de **saúde** — rodando inteiramente
sobre **dados de paciente inventados**. Ele permite que um assistente:

- **encontre pacientes** por nome ou condição, e puxe seus dados demográficos, condições,
  consultas e resultados de exames;
- **marque uma consulta** ou **registre um resultado de exame** — ações que *alteram dados*;
- **rode um relatório de coorte** (ex.: "quantos pacientes têm diabetes?") como tarefa em
  segundo plano;
- leia o prontuário de um paciente em **FHIR**, o formato que sistemas hospitalares reais
  trocam entre si.

O ponto não são as funcionalidades de saúde em si — é *o quão cuidadosamente* elas são
feitas. Num campo regulado, deixar uma IA tocar em registros só é aceitável com guardrails,
então toda ação aqui é **validada antes de rodar**, **registrada com os dados pessoais
mascarados**, **protegida por login e permissões** e — para tudo que altera dados —
**sinalizada para que o app pergunte ao humano "tem certeza?" antes**. E esses guardrails
não são só afirmados: uma suíte de ataques simulados roda a cada build e **reprova o build
se algum guardrail vazar**.

### Um passo a passo concreto

Um clínico, conversando com o Claude, pede: *"Resuma o paciente p-001 para triagem."*

1. O Claude escolhe o prompt `triage_summary`, que o instrui a reunir os dados do jeito certo.
2. Ele chama `get_patient("p-001")` e `list_appointments("p-001")`, e lê o resource de
   exames. Nos bastidores, o servidor confere que o token de acesso do chamador tem a
   permissão `patients:read`, valida cada argumento e escreve uma linha de auditoria com o
   nome mascarado (`R****** A******`).
3. O Claude redige o resumo e sugere marcar um retorno. Marcar é uma *escrita*, então é
   marcada como consequente — o host **pausa e pede a confirmação do clínico** antes de o
   `book_appointment` realmente rodar (e essa chamada exige a permissão mais forte,
   `appointments:write`).
4. Se o Claude tentasse registrar um exame com um código médico inventado, o servidor
   **rejeitaria** em vez de deixar um código fabricado entrar no registro.

### Para quem é

É uma **implementação de portfólio / referência**: uma demonstração de como construir uma
integração MCP do jeito que um domínio regulado (saúde, finanças, jurídico) de fato exige —
segurança, auditabilidade e supervisão humana tratadas como primeira classe, não como
remendo. **Não** é um produto médico e nunca deve ser apontado para dados de paciente reais.

## Por que este projeto existe

O MCP é a forma padrão de conectar modelos de IA a sistemas reais (doado à Linux Foundation
em dez/2025; ~10 mil servidores públicos; adoção em produção pelos principais hosts). A
habilidade escassa é fazer isso *bem* onde o erro custa caro. Este repo demonstra integração
MCP com a disciplina de um domínio regulado — **segurança como primitiva, e verificável, não
apenas afirmada**. Ele mira exatamente onde o ecossistema é fraco: só ~8,5% dos servidores
MCP implementam o OAuth 2.1 obrigatório, e ataques de tool poisoning têm mais de 60% de
sucesso mundo afora.

É a metade operacional de uma história de dois repos: `llm-guardrails` governa *o que o
modelo responde*; este servidor governa *o que o modelo pode executar via ferramentas*.

## O que ele expõe

| Primitiva | Nome | Escopo | Observação |
|-----------|------|--------|------------|
| Tool (leitura) | `search_patients(query)` | `patients:read` | Busca por nome ou condição. |
| Tool (leitura) | `get_patient(patient_id)` | `patients:read` | Dados demográficos e condições. |
| Tool (leitura) | `list_appointments(patient_id, from_date?, to_date?)` | `patients:read` | Faixa de data opcional. |
| Tool (**escrita**) | `book_appointment(patient_id, when, reason)` | `appointments:write` | **Consequente** — o host confirma. |
| Tool (**escrita**) | `record_lab_observation(patient_id, loinc_code, value, …)` | `appointments:write` | **Consequente**; código LOINC validado (anti-alucinação). |
| Tool (task) | `start_cohort_report(condition)` / `get_cohort_report(task_id)` | `patients:read` | Agregado de longa duração via o padrão Tasks. |
| Resource | `patient://{patient_id}/labs` | `patients:read` | Resultados de exames, endereçáveis por URI. |
| Resource | `fhir://Patient/{patient_id}` | `patients:read` | Paciente como Bundle FHIR R4. |
| Resource | `ui://appointment/confirm/{patient_id}` | `patients:read` | Confirmação HTML renderizada no servidor (precursor de MCP App). |
| Prompt | `triage_summary(patient_id)` | — | Template estruturado de triagem. |

## Como rodar

### Local (stdio, sem auth) — um comando

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     |  macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m mcp_health_server
```

### Remoto (Streamable HTTP, Resource Server OAuth 2.1)

```bash
MCP_TRANSPORT=streamable-http python -m mcp_health_server
```

Ele imprime um token bearer de dev (leitura+escrita) no stderr e serve em
`http://127.0.0.1:8000/mcp`. Requisições sem token válido recebem **HTTP 401**. O enforce
é real:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/mcp \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# => 401
```

A configuração vem do ambiente:

| Variável | Padrão | Finalidade |
|----------|--------|------------|
| `MCP_TRANSPORT` | `stdio` | `stdio` ou `streamable-http`. |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `8000` | Endereço de bind do HTTP. |
| `MCP_HEALTH_DATA_PATH` | `data/patients.json` embutido | Caminho do dataset sintético. |
| `MCP_HEALTH_LOG_LEVEL` | `INFO` | Verbosidade do log de auditoria. |
| `MCP_AUTH_ENABLED` | desligado | Liga o enforce de escopo por ferramenta (o HTTP liga sozinho). |
| `MCP_AUTH_ISSUER` / `MCP_AUTH_AUDIENCE` | padrões de dev | Issuer e audience do OAuth (RFC 8707). |
| `MCP_OTEL_EXPORTER` | `none` | Exportador de tracing: `none` \| `console` \| `otlp`. |

> O token de dev é emitido por um **Authorization Server mock** em processo, só para
> dev/CI. Um deploy real valida contra o JWKS de um issuer real e nunca usa o mock.

Ajustes extras de HTTP: `MCP_HTTP_STATELESS=1` roda o servidor sem sessão
(`Mcp-Session-Id`), para que ele possa ficar atrás de um load balancer round-robin simples
— a direção que a spec de 2026-07-28 formaliza.

### Container (Docker)

```bash
docker compose up                          # só o servidor, tracing no console
docker compose --profile observability up  # servidor + coletor OTLP
```

O container roda o transporte Streamable HTTP como usuário não-root e imprime um token
bearer de dev nos logs ao subir.

### Conectar pelo MCP Inspector

```bash
npx @modelcontextprotocol/inspector python -m mcp_health_server
```

### Conectar pelo Claude Desktop

Adicione em `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "health": { "command": "python", "args": ["-m", "mcp_health_server"] }
  }
}
```

Como `book_appointment` é anotada como consequente, o host pede confirmação.

## Segurança e conformidade — o coração do repo

1. **Validação estrita no servidor em toda ferramenta.** O modelo nunca é confiável.
   Paciente inexistente, id malformado, strings de injeção e faixa de data invertida
   devolvem erros limpos, não exceções cruas.
2. **Log de auditoria de toda invocação, com PII redigida.** Nomes mascarados
   (`Rafaela Almeida` → `R****** A******`), data de nascimento → `****-**-**`. Os logs vão
   para o **stderr** (o stdout é o canal JSON-RPC no stdio).
3. **Config e segredos vêm só do ambiente** — nunca de schema de tool ou payload de
   resource. `.env` está no gitignore.
4. **A ferramenta de escrita é consequente** (`destructiveHint=True`) — human-in-the-loop.
5. **OAuth 2.1 como Resource Server.** Validação local de JWT RS256, enforce de `aud`
   (RFC 8707, anti-replay) e **escopos por ferramenta** (`patients:read` vs
   `appointments:write`) — menor privilégio nos dois sentidos.
6. **Observabilidade sem PII.** Um span OpenTelemetry por chamada, registrando nome da
   ferramenta, escopo, resultado e latência — nunca argumentos ou resultado.
7. **Guardrails verificáveis.** Uma suíte de red-team (`tests/redteam/`) reproduz tool
   poisoning, escalação de autorização e vazamento de PII. Ela roda como **gate no CI**:
   qualquer ataque bem-sucedido reprova o build. Um meta-teste prova que o gate é
   load-bearing (afrouxá-lo inverte o resultado).

### Mapeamento para ISO/IEC 42001 + HIPAA + LGPD

| Controle | Como o servidor atende |
|---|---|
| **HIPAA — trilha de auditoria de todo acesso a PHI** | `@audited` + um span por chamada, com PII redigida. |
| **HIPAA — mínimo necessário** | Escopos por ferramenta; `PatientSummary` enxuto. |
| **HIPAA — controle de acesso** | Resource Server OAuth 2.1, enforce de escopo por ferramenta. |
| **LGPD — minimização e finalidade** | Redação de PII em logs/traces; dado sintético; escopo por ferramenta. |
| **ISO/IEC 42001 — supervisão humana (A.9.2)** | Escrita consequente (HITL) + gate de red-team. |
| **ISO/IEC 42001 — segurança do sistema (B.6.2.6)** | Auth, validação estrita, resistência a poisoning verificada no CI. |

> Ilustrativo de uma postura de controles alinhada à ISO/IEC 42001, não uma certificação
> formal. Não existe "IA certificada HIPAA" — conformidade é um estado operacional, que é
> exatamente o que os controles em volta do modelo demonstram.

## Dados sintéticos

Tudo em [`data/patients.json`](data/patients.json) é inventado — seis pacientes fictícios
com consultas e exames. **Nunca** aponte `MCP_HEALTH_DATA_PATH` para dados reais.

## Testes

```bash
pytest                    # suíte completa (cliente in-memory, sem transporte)
pytest tests/redteam -q   # o gate de segurança adversarial, isolado
```

Cobertura: o servidor responde com os modelos esperados; entrada ruim é rejeitada em vez de
quebrar; a linha de auditoria mascara o nome do paciente; a verificação de token rejeita
audience errada, token expirado, issuer errado e token forjado; escopos por ferramenta
impõem menor privilégio; e a suíte de red-team prova resistência a poisoning/escalação/
vazamento de PII.

## Decisões de design e trade-offs

Veja [DESIGN.md](DESIGN.md) — camada fina, transportes, segurança como primitiva
*verificável*, auth de Resource Server, dado só sintético e a decisão de versão do SDK,
cada uma com o seu "por que não do outro jeito".

## Realismo FHIR e conceitos de v2 (construídos)

- **FHIR (v1.5):** `fhir://Patient/{id}` retorna um Bundle FHIR R4 (Patient + Conditions +
  Observations). A ferramenta de escrita `record_lab_observation` **valida o código LOINC
  contra um conjunto conhecido e rejeita códigos fabricados** — o controle nomeado de
  anti-alucinação para IA clínica. O dado continua sintético; `data.py` é a única costura
  que um backend FHIR/EHR real substituiria.
- **HTTP stateless (v2):** `MCP_HTTP_STATELESS=1` — real, usando o `stateless_http` do SDK.
- **Padrão Tasks (v2):** `start_cohort_report` devolve um handle; `get_cohort_report` faz o
  poll, usando as próprias strings de status do protocolo (`working`/`completed`/…).
- **Precursor de MCP App (v2):** `ui://appointment/confirm/{id}` serve um card HTML de
  confirmação sem PII.

> **Nota de honestidade sobre a v2.** O SDK v2 do MCP (núcleo stateless nativo, Tasks
> nativas, MCP Apps renderizadas no servidor) ainda não foi publicado — este repo fixa a
> linha estável v1 (`mcp>=1.28,<2.0`). Os itens acima implementam os *conceitos* da v2
> sobre o SDK estável; a migração nativa é um passo futuro deliberado, quando a v2 sair. O
> caminho de HITL interativo suportado hoje é elicitation (`Context.elicit`); Apps nativas
> chegam com a v2.

## Ainda futuro (não construído)

- Migrar para o SDK v2 do MCP quando publicado (núcleo stateless / Tasks / MCP Apps nativos).
- Coordenação multi-agente (agent-to-agent).
- Backend FHIR/EHR real atrás da costura `data.py`; Authorization Server / IdP externo real;
  JWKS buscado+cacheado de um issuer ao vivo em vez do mock em processo.
