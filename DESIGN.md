# DESIGN — mcp-health-server (v0)

Uma RFC leve. Registra por que o servidor tem a forma que tem e — a parte que sinaliza
senioridade — por que ele *não* tem as outras formas plausíveis.

## Contexto

Um modelo de IA que só conversa tem uso limitado num cenário operacional; ele fica útil
quando consegue *agir* sobre sistemas reais. Há duas formas de dar esse alcance:

- **Function calling específico de um provedor** — definir ferramentas dentro da API de um
  fornecedor. Não é portável para nada; reimplementado por host.
- **MCP** — um protocolo agnóstico de host (JSON-RPC 2.0) com um vocabulário compartilhado
  de tools, resources e prompts. Um servidor, todo host compatível.

Escolhemos MCP porque portabilidade é o ponto inteiro de uma camada de integração, e porque
o MCP está se consolidando como o padrão da indústria para isso.

O domínio — saúde — muda o nível de exigência de engenharia. Num domínio regulado, um
argumento não validado ou um nome vazando para um arquivo de log não é um bug, é um
incidente. Por isso o design trata **validação e auditoria como primitivas**, presentes
desde a primeira ferramenta, não como recursos adicionados depois.

## Decisões

1. **Camada fina de tradução.** `data.py` guarda a "lógica de negócio" (a fonte de dados
   sintética) e não importa nada de MCP. `tools.py`/`resources.py`/`prompts.py` só validam
   a entrada, delegam para `data.py` e devolvem modelos tipados. Um `GET /patient/{id}` REST
   mapeia um-para-um numa tool `get_patient(id)`. Isso mantém o servidor portável e faz de
   um futuro backend FHIR/EHR uma troca atrás de uma única costura.

2. **Transporte stdio na v0.** A coisa mais simples que um host local (Claude Desktop, o
   Inspector) consegue dirigir, e a mais fácil de demonstrar. Streamable HTTP fica
   documentado como o caminho de rede, mas não construído — veja trade-offs.

3. **Segurança como primitiva.**
   - Modelos Pydantic + validadores explícitos (`validate_patient_id`, `validate_date_range`)
     rejeitam entrada ruim no servidor. O modelo nunca é confiável.
   - Um decorator `@audited` envolve toda tool e loga cada chamada com PII redigida. Ele fica
     *abaixo* do `@mcp.tool`, então o FastMCP ainda deriva o schema da assinatura real.
   - Config/segredos vêm só de variáveis de ambiente.
   - A única tool de escrita carrega `destructiveHint=True` para o host pedir confirmação.

4. **Só dado sintético.** O dataset é fictício e rotulado como tal no topo do JSON e no
   README. Nenhum dado de paciente real, jamais.

5. **Versão do SDK fixada na linha v1 (`mcp>=1.28,<2.0`).** O SDK Python do MCP está
   caminhando para uma v2 que acompanha a spec de 2026-07-28 e carrega mudanças que quebram
   compatibilidade. Fixar em `<2.0` é uma decisão consciente de construir sobre a linha
   estável e evita que um `pip install` no dia errado quebre o repo silenciosamente. Subir
   para a v2 será uma migração deliberada e guiada, não uma deriva.

## Alternativas consideradas

- **Usar um servidor MCP pronto.** Rejeitado: o valor aqui é justamente o rigor de domínio
  regulado (validação + auditoria com PII redigida + sinalização de escrita consequente).
  Um servidor genérico não demonstra isso.
- **Expor os dados como uma API REST comum.** Rejeitado: REST não é agnóstico de host para
  modelos de IA — cada host precisaria de cola sob medida. O MCP é o contrato compartilhado.
  (A camada fina significa que uma fachada REST poderia ser adicionada depois sem tocar nas
  tools.)
- **Proibir argumentos desconhecidos no schema (`extra="forbid"`).** Considerado. O modelo
  de argumentos gerado pelo SDK ignora campos extras (JSON-RPC tolerante). Em vez de fazer
  monkeypatch nos internos do SDK, contamos com o fato de que o modo de falha *relevante* —
  o modelo alucinar um nome de parâmetro — aparece como argumento obrigatório ausente e é
  rejeitado. Documentado e testado assim.
- **Logar no stdout / num arquivo por padrão.** Rejeitado: o stdout é o canal JSON-RPC no
  stdio e precisa ficar limpo; um arquivo de log padrão arrisca persistir dado operacional.
  A auditoria vai para o stderr; um sink em arquivo é uma decisão opcional de deploy.

## Trade-offs

**Ganhamos:** portabilidade entre hosts MCP; uma postura de segurança que mapeia para a
ISO/IEC 42001; clareza da costura de camada fina; testes rápidos e sem transporte via o
cliente in-memory.

**Abrimos mão (de propósito, na v0):** sem transporte de rede (só stdio), sem auth, sem
backend de dado real, e tratamento tolerante de argumentos extras em vez de rejeição
estrita. Cada um é uma fronteira escopada da v0, não um descuido — o "por que não agora"
está registrado acima para que o próximo incremento parta de uma linha de base intencional.

---

# v1 — remoto, autorizado e *verificavelmente* seguro

A v1 transforma a prova da v0 num artefato de nível de produção e contratável, fechando as
lacunas que o mercado exige e onde o ecossistema é mais fraco: transporte, autorização,
observabilidade e — o diferencial — **segurança que você roda no CI**.

## Decisões (v1)

1. **Dois transportes, um servidor.** `build_server` é agnóstico de transporte; o entrypoint
   escolhe stdio (dev, sem auth) ou Streamable HTTP (remoto, auth ligada) por `MCP_TRANSPORT`.
   Nada nas tools muda — a camada fina compensa.

2. **OAuth 2.1 como *Resource Server*, não Authorization Server.** O servidor valida tokens
   (assinatura RS256, `exp`, `iss` e `aud` conforme RFC 8707 para barrar replay entre
   recursos) e faz enforce de escopos; ele nunca autentica usuário nem emite token. A
   validação usa PyJWT — sem cripto artesanal. Só ~8,5% dos servidores MCP fazem isso, então
   fazer certo é um forte sinal de senioridade.

3. **Escopos por tool, com enforce em processo.** `require_scope` lê o contexto de auth do
   SDK e protege cada tool (`patients:read` vs `appointments:write`). Fica abaixo do
   `@audited`, então as negações são auditadas, e é no-op quando a auth está desligada, de
   modo que stdio/dev continua funcionando. O marcador `__required_scope__` propaga pela
   cadeia de `functools.wraps` para o tracer registrar.

4. **Segurança *verificável*, não afirmada.** `tests/redteam/` reproduz os ataques reais de
   2026 — tool poisoning, escalação de autorização, vazamento de PII — e roda como gate no
   CI. Um meta-teste (`test_gate_is_real`) mostra que o resultado inverte quando o guardrail
   é afrouxado, então um verde não pode ser vazio.

5. **Observabilidade sem PII.** Um span por chamada só com atributos não sensíveis,
   espelhando o outcome da auditoria. O tracer usa um provider próprio do módulo (não o
   global set-once do OTel), para os testes capturarem spans de forma determinística.

## Alternativas consideradas (v1)

- **Rolar a própria validação de token / Authorization Server.** Rejeitado — todo guia de
  segurança MCP diz para usar bibliotecas testadas; verificamos com PyJWT e deixamos a
  emissão para um IdP real (um AS mock cobre só dev/CI, nunca produção).
- **Fazer enforce de auth só na camada de transporte.** O SDK de fato protege as requisições
  HTTP (verificado: sem auth → 401), mas isso sozinho não expressa escopos *por tool*, e não
  roda sob o cliente de teste in-memory. O `require_scope` por tool dá tanto autorização
  diferenciada quanto testabilidade independente de transporte.
- **Testar auth ponta a ponta pelo cliente in-memory.** A sessão do cliente roda as tools
  numa task separada onde o contextvar de auth não propaga, então testes de escopo seriam
  impossíveis de montar honestamente. Testamos o verifier como unidade e o gate de escopo
  nas funções das tools dentro de um contexto `principal()` — ambos checagens reais e
  herméticas.

## Trade-offs (v1)

**Ganhamos:** deployabilidade remota; uma postura real de auth de Resource Server; escopos de
menor privilégio; tracing estilo produção; e uma suíte de segurança que *reprova o build* na
regressão — a diferença entre marketing e engenharia.

**Abrimos mão (adiado por design):** o JWKS vem de um mock em processo em vez de ser
buscado+cacheado de um issuer ao vivo (drop-in depois); ainda sem realismo FHIR nem
containerização (v1.5); ainda na linha v1 do SDK — a migração stateless da v2 é um passo
futuro deliberado, não uma deriva.

---

# v1.5 — realismo FHIR + container

## Decisões (v1.5)

1. **Forma FHIR, dado sintético.** `fhir.py` mapeia os modelos de domínio para recursos FHIR
   R4 e um Bundle. O mapeamento fica do lado das tools/resources da costura `data.py`, então
   um backend FHIR real depois substitui `data.py` sem tocar nos mappers.
2. **Validação de código clínico como controle anti-alucinação.** A escrita
   `record_lab_observation` valida o código LOINC contra um conjunto conhecido e recusa
   códigos desconhecidos. Códigos médicos fabricados são um risco nomeado de IA clínica;
   rejeitá-los antes da escrita é o análogo, específico do domínio, da validação de entrada
   do resto.
3. **O container roda o transporte HTTP como não-root**, com um coletor OTLP opcional sob um
   profile do compose, de modo que `docker compose up` funciona sozinho.

## Trade-offs (v1.5)

**Ganhamos:** o formato de intercâmbio que sistemas reais falam; uma proteção concreta contra
alucinação de código clínico; uma demo remota reproduzível.
**Abrimos mão:** um allowlist de códigos sintético e minúsculo em vez de um servidor de
terminologia real; sem semântica de *busca* FHIR (só um Bundle por paciente).

---

# v2 — conceitos da spec-2026-07-28 sobre um SDK v1 (escopo honesto)

A spec de 2026-07-28 adiciona um núcleo stateless, um protocolo Tasks nativo e MCP Apps
renderizadas no servidor. **O SDK v2 que os implementa ainda não foi publicado** (este repo
fixa `mcp>=1.28,<2.0`), então a v2 aqui constrói os *conceitos* sobre o SDK estável e marca
com precisão o que espera pela migração real.

## Decisões (v2)

1. **HTTP stateless — real.** `stateless_http` existe na 1.28; `MCP_HTTP_STATELESS=1` o
   habilita. É o único pedaço do "núcleo stateless" disponível hoje.
2. **Tasks — forma no nível da aplicação, alinhada ao protocolo.** Os *tipos* de Tasks
   nativos vêm em `mcp.types`, mas a API de alto nível do FastMCP ainda não expõe tools em
   modo task. Então uma tool de início devolve um handle e uma tool de get faz o poll,
   usando as próprias constantes de status do protocolo (`working`/`completed`/`failed`).
   Migrar para Tasks nativo na v2 é um rename, não um redesign.
3. **MCP App — precursor, não nativo.** Não existe tipo App nativo na 1.28, então a UI de
   confirmação é um fragmento HTML servido como resource, mantido **sem PII** (só o id do
   paciente) para nada sensível viajar no payload auditado. O caminho de HITL interativo
   genuíno hoje é elicitation (`Context.elicit`); Apps renderizadas no servidor chegam com
   a v2.

## Alternativas consideradas (v2)

- **Migrar de fato para o SDK v2 agora.** Impossível — não está no PyPI. Fingir uma migração
  ou vendorar um SDK não lançado seria desonesto e frágil. Esperamos e documentamos.
- **Colocar dados do paciente na UI de confirmação.** Rejeitado — um resultado HTML de texto
  livre contorna o redator por chave de dict e vazaria para o log de auditoria. O fragmento
  referencia o paciente só pelo id.

## Trade-offs (v2)

**Ganhamos:** deployabilidade stateless hoje; uma API com forma de Tasks que sobe limpo; um
precursor tangível de App.
**Abrimos mão (até o SDK v2 sair):** ciclo de vida/notificações de Tasks nativas, Apps
nativas renderizadas no servidor, e o núcleo de protocolo stateless além da flag de
transporte.
