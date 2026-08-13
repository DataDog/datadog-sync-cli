# Contexto — Pre-flight Check de configs não-migráveis (datadog-sync-cli)

> Documento de contexto/decisão. **Nada foi implementado.** Registra a análise feita e o
> desenho acordado para um script auxiliar de verificação de pré-requisitos.
> Data: 2026-08-13.

---

## 1. Objetivo

Antes de rodar uma migração com `datadog-sync-cli`, verificar se **todas as configurações
não-migráveis** (pré-requisitos manuais) da org de **origem** já existem/estão configuradas
na org de **destino** — para evitar que a migração falhe ou que recursos fiquem quebrados
silenciosamente (ex.: monitor referenciando canal do Slack que não existe no destino).

A verificação cobre **apenas o que a ferramenta NÃO migra**. Recursos migráveis (monitors,
dashboards, SLOs, users…) já são cobertos pelo comando nativo `diffs` e ficam fora deste escopo.

---

## 2. Análises que embasaram o contexto

### 2.1 Migração de monitores preserva estado muted/unmuted?

- O estado de mute vive em `options.silenced` (dict): `{}` = unmuted; `{"*": null}` = muted
  forever; `{"*": <epoch>}` = muted até timestamp; `{"host:x": <epoch>}` = mute com escopo.
- Em `datadog_sync/model/monitors.py` os `excluded_attributes` são: `id, assets,
  matching_downtimes, creator, created, deleted, org_id, created_at, modified, overall_state,
  overall_state_modified`. **`options.silenced` NÃO está excluído** → é copiado verbatim.
- `prep_resource` → `remove_excluded_attr` (resource_utils.py) só remove os excluídos; o
  `create_resource` faz POST do recurso inteiro. **Logo, muted/unmuted é preservado.**
- Ressalva: mutes com timestamp absoluto (`{"*": <epoch>}`) copiam o epoch da origem; se já
  expirou no momento do sync, o destino fica efetivamente unmuted. "Forever" e "unmuted"
  preservam exatamente. Cassettes de teste só cobrem o caso `{}`.

### 2.2 Quais recursos o script migra (38 tipos)

Diretório `datadog_sync/model/`. Grupos: Monitoramento (monitors, SLOs, slo_corrections,
downtime_schedules, downtimes*), Dashboards (dashboards, dashboard_lists, notebooks,
powerpacks), Logs (logs_pipelines+order, logs_indexes+order, logs_archives+order,
logs_metrics, logs_restriction_queries, logs_custom_pipelines*), Métricas (metrics_metadata,
metric_tag_configurations, metric_percentiles, spans_metrics), Synthetics (tests,
global_variables, private_locations, mobile_applications+versions, test_suites), RUM
(rum_applications), Segurança/SDS (security_monitoring_rules, sensitive_data_scanner_*,
restriction_policies), Usuários & acesso (users, roles, teams, team_memberships,
authn_mappings), host_tags. (* = deprecated). Lista oficial: README.md:226-262.

### 2.3 O que NÃO é migrado (respostas às perguntas do usuário)

- **API keys / App keys**: NÃO migram — são credenciais de ENTRADA da ferramenta
  (`DD_*_API_KEY` / `DD_*_APP_KEY`); criar manualmente no destino.
- **Integrações (conexão/credencial)**: NÃO migram. `logs_archives` exige AWS/GCP/Azure
  configurado manualmente (README.md:282). `logs_pipelines` sincroniza só a config de
  processamento (pipelines OOTB/custom), não a conexão da integração.
- **Usuários**: SIM migram (users/roles/teams/team_memberships/authn_mappings). Ressalvas:
  usuários desabilitados são pulados (users.py:90); MFA/verificação/last_login não migram;
  service accounts via endpoint dedicado (precisa permissão `service_account_write`, senão
  HTTP 403 em users.py:178); tipo de conta é read-only.

### 2.4 Pré-requisitos para a migração não falhar

A ferramenta remapeia IDs internos (monitor→SLO, dashboard→monitor, composite→membros,
principais de roles/users/teams), mas NÃO recria nem valida dependências externas nem
handles de notificação.

| # | Pré-requisito no destino | Se faltar |
|---|---|---|
| 1 | Integrações de notificação (Slack, PagerDuty…) com mesmos handles | Monitor falha (400) ou não notifica |
| 2 | Integração de cloud (AWS/GCP/Azure) p/ logs_archives | Archive falha |
| 3 | API/App keys criadas, com permissões corretas | Auth falha / 403 |
| 4 | Dependências incluídas no sync (SLOs, monitores-membro, roles, users…) | failed_connections / referência dropada |
| 5 | Users/roles/teams resolvíveis p/ restriction_policies | Hard-fail ou drop de principal |
| 6 | Workers de Private Location reconfigurados (datadogHostOverride) | Testes não rodam |
| 7 | Nenhum org em DDR; region/URL corretos | Comando aborta (--verify-ddr-status, README.md:210) |
| 8 | Reinstrumentar RUM (novos IDs regenerados) | Dados RUM não chegam |

Detalhe do caso Slack (exemplo do usuário): o campo `message` do monitor (com `@slack-...`)
NÃO está nos excluded_attributes e NÃO é remapeado — vai literal. Se o destino valida o
handle na criação, o POST /api/v1/monitor retorna 400 e o monitor falha. Se passar, a
notificação simplesmente não é entregue até configurar a integração.

---

## 3. Decisão: Opção A — script standalone, read-only, isolado

Descartadas (registradas como alternativas): Opção B (novo subcomando `preflight` integrado
via ALL_COMMANDS + enum Command + elif em run_cmd_async — aditivo, mas mexe em arquivos
existentes); Opção C (reusar estado já importado em `resources/source/`).

**Escolhida: Opção A.** Arquivo novo e isolado (ex.: `scripts/preflight_check.py`).
**Nenhuma linha dos arquivos atuais é modificada.** Não entra em `ALL_COMMANDS`, `run_cmd`
nem `ResourcesHandler`. Reusa `CustomClient` como biblioteca. Só GETs.

### 3.1 Escopo confirmado

Comparação **exclusivamente das configs não-migráveis**. Os monitors da origem são usados
apenas como FONTE das referências (`@handles`); o script então verifica se o DESTINO tem a
integração correspondente. Sem sobreposição com `sync`/`diffs`.

### 3.2 Conexão aos 2 ambientes (variáveis)

Dois `CustomClient` independentes, um por org, mesmas variáveis que a ferramenta já usa:

```
Origem:                          Destino:
  DD_SOURCE_API_URL                DD_DESTINATION_API_URL
  DD_SOURCE_API_KEY                DD_DESTINATION_API_KEY
  DD_SOURCE_APP_KEY                DD_DESTINATION_APP_KEY
```

- Dict de auth no formato real (configuration.py:462-494): `{"apiKeyAuth": ..., "appKeyAuth": ...}`.
- Construtor: `CustomClient(host, auth, retry_timeout, timeout, send_metrics)` (custom_client.py:153).
- Ciclo: `await client._init_session()` antes do 1º GET; `await client._end_session()` no fim.
- **Garantia de isolamento**: só `client.get(...)` (custom_client.py:240). Nunca post/put/patch/delete.

### 3.3 Fluxo de chamadas (3 fases + relatório)

```
preflight_check.py
│
├── 1. build_clients()
│      source_client      = CustomClient(SOURCE_URL, source_auth, ...)
│      destination_client = CustomClient(DEST_URL,   dest_auth,   ...)
│      await both._init_session()
│
├── 2. COLETA (só na ORIGEM) — extrai referências não-migráveis
│      monitors = await source_client.paginated_request(source_client.get)("/api/v1/monitor", ...)
│      handles  = regex "@[\w-]+..." sobre cada monitor["message"]
│      → conjunto distinto agrupado por tipo: {slack:[...], pagerduty:[...], webhook:[...], team:[...]}
│      (opcional) varrer também dashboards/downtimes pelo mesmo campo
│
├── 3. VERIFICAÇÃO (só no DESTINO) — só GETs
│      GET /api/v1/integration/slack/...              → canais instalados
│      GET /api/v1/integration/pagerduty              → services
│      GET /api/v1/integration/webhooks/configuration → webhooks
│      GET /api/v1/integration/{aws,gcp,azure}        → contas de cloud (p/ logs_archives)
│      GET .../synthetics/locations/private (origem E destino) → contagem/identidade de PLs
│
└── 4. COMPARA + RELATÓRIO
       gap[categoria] = referencias_origem[categoria] − instalados_destino[categoria]
       imprime relatório + exit(1) se houver gaps (exit(0) se ok)
       finally: await both._end_session()
```

### 3.4 Exemplo de saída

```
PREFLIGHT — configs não-migráveis (origem → destino)

[SLACK]      2 gaps
  @slack-prod-alerts   ← faltando no destino (usado por 14 monitores)
  @slack-db-oncall     ← faltando no destino (usado por 3 monitores)
[PAGERDUTY]  OK (3/3 services presentes)
[CLOUD/AWS]  1 gap
  account 1234567890   ← integração AWS ausente (bloqueia logs_archives)
[WEBHOOKS]   OK
[PRIVATE LOCATIONS]  contagem: origem=5  destino=3  → 2 ausentes no destino (sanity check)

Resultado: 3 gaps encontrados → exit code 1
```

### 3.5 Parâmetros de entrada

| Variável / flag | Origem | Uso |
|---|---|---|
| `DD_SOURCE_API_URL/API_KEY/APP_KEY` | env | cliente da origem (lê monitors) |
| `DD_DESTINATION_API_URL/API_KEY/APP_KEY` | env | cliente do destino (lê integrações) |
| `--report-format` (opcional) | flag | `text` (default) ou `json` p/ CI |
| `--fail-on-gap` (opcional) | flag | controla se `exit(1)` quando há gap |

### 3.6 Private Locations — contagem como sanity check PADRÃO

Decisão do usuário: incluir por PADRÃO no pre-check a **contagem/identidade** das Private
Locations em ambos os lados.

- `origem  = GET .../synthetics/locations/private` → conta / lista de PLs.
- `destino = GET .../synthetics/locations/private` → conta / lista de PLs.
- `gap = definições na origem ausentes no destino`. Reporta `origem=N destino=M` + faltantes.
- **O que cobre**: a DEFINIÇÃO da PL (nome, id, tags, metadata). Pega o caso "esqueci de
  sincronizar as PLs". Observação: a definição é um recurso MIGRÁVEL
  (`synthetics_private_locations`), então há overlap com o comando nativo `diffs` — aqui entra
  apenas como sinal precoce leve, por escolha explícita.
- **O que NÃO cobre**: se o WORKER (binário na infra do cliente) está ativo e reportando ao
  destino, nem se o `datadogHostOverride` aponta para a região certa. A API pode listar a PL
  como existente enquanto nenhum worker está de pé. Isso continua sendo verificação MANUAL na
  infraestrutura — a contagem NÃO substitui validar o worker.

### 3.7 Fora de escopo por design

- RUM (`client_token`/`application_id`): regenerados, sem "gap" a medir.
- Worker de Private Location (liveness/health do binário): roda na infra do cliente, sem API.
  (A CONTAGEM das definições de PL entra por padrão — ver 3.6.)
- MFA/verificação de usuário: estado read-only não comparável.
- Demais recursos migráveis: já cobertos pelo comando `diffs`.

### 3.8 Salvaguardas ("não impacta a migração")

- Arquivo novo e isolado; nenhuma linha dos arquivos atuais é tocada.
- Só GET. Sem leitura/escrita de `state`. Sem escrita em disco além do relatório.
- Falha do pre-flight não aborta sync (execução separada).
- Reusa `CustomClient` via import, não por modificação.
- Testes próprios (cassettes VCR novos), sem tocar nos cassettes existentes.

---

## 4. Referências de código (âncoras)

- `datadog_sync/model/monitors.py` — excluded_attributes:58-70; create_resource:207-220;
  update_resource:222-233.
- `datadog_sync/utils/resource_utils.py` — prep_resource/remove_excluded_attr:220-230;
  check_diff:294-302; create_global_downtime:163.
- `datadog_sync/utils/base_resource.py` — build_excluded_attributes:138-141.
- `datadog_sync/utils/resources_handler.py` — prep_resource+diff no apply:503-509.
- `datadog_sync/utils/custom_client.py` — CustomClient.__init__:153; get:240; _init_session:189;
  paginated_request:277; build_default_headers:458.
- `datadog_sync/utils/configuration.py` — Configuration:55; build_config auth:450-496.
- `datadog_sync/commands/` — diffs.py (padrão de comando); shared/utils.py run_cmd:9 /
  run_cmd_async:35; __init__.py ALL_COMMANDS:14.
- `datadog_sync/model/users.py` — disabled skip:90; service_account 403:178; excluded:37-57.
- `README.md` — recursos:226-262; dependências:274-304; DDR:210; logs_archives manual:282.
- `docs/synthetics-private-locations.md` — replicação de PLs + datadogHostOverride.

---

## 5. Estado atual / próximos passos

- **Status: OPÇÃO A IMPLEMENTADA** em `scripts/preflight_check.py` (2026-08-13).
  Arquivo novo e isolado; nenhum arquivo existente foi modificado. Não entra em
  `ALL_COMMANDS`/`run_cmd`/`ResourcesHandler`. Só GETs; reusa `CustomClient` + `PaginationConfig`.
- **O que a v1 faz:**
  - Fase 1: lê monitores da ORIGEM (`GET /api/v1/monitor`, paginação igual à de Monitors) e
    extrai handles de notificação do campo `message` via regex, classificando por tipo
    (slack/pagerduty/webhook/opsgenie/teams/email/other) com contagem de monitores por handle.
  - Fase 2: enumera integrações no DESTINO em best-effort (PagerDuty, webhooks, Slack) e calcula
    o gap onde a API responde; onde não responde (404/403/deprecado), marca "não verificável"
    sem abortar. Cloud AWS/GCP/Azure: reporta contagem de contas configuradas.
  - Fase 3: contagem/identidade de Private Locations origem×destino
    (`GET /api/v1/synthetics/locations`, filtra ids `^pl:`), lista PLs ausentes no destino.
  - Saída: relatório `text` (default) ou `json` (`--report-format`).
  - Exit codes: 0 = sem gaps; 1 = gaps encontrados (a menos que `--no-fail-on-gap`);
    2 = erro de execução (credenciais/rede).
- **Variáveis:** `DD_SOURCE_API_KEY/APP_KEY/API_URL`, `DD_DESTINATION_API_KEY/APP_KEY/API_URL`
  (e `DD_*_JWT` opcional, com precedência sobre API key). URL default: https://api.datadoghq.com.
- **Validação feita:** import OK; `--help` OK; sem credenciais → exit 2 com mensagem clara;
  teste offline da extração de handles + renderização text/json + `_has_gaps` (com dados
  simulados) OK; `py_compile` OK; nenhuma linha > 120. Caminho de rede (chamadas reais à API)
  NÃO testado por falta de credenciais — validar em ambiente real antes de confiar nos gaps.
- **Confirmado:** contagem de Private Locations entra por PADRÃO (ver 3.6) — já incluída.
- **Pontos em aberto / melhorias futuras:**
  - Varrer também dashboards/downtimes pelo mesmo campo de handles (hoje só monitores).
  - Endurecer os endpoints de integração (Slack em especial varia por org/versão; hoje é
    best-effort e pode cair em "não verificável").
  - Adicionar testes com cassettes VCR próprios (sem tocar nos cassettes existentes).
  - Eventual promoção para subcomando `preflight` (Opção B) se virar parte oficial do fluxo.
