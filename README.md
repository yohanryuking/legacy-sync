# legacy-sync

🇪🇸 Versão em espanhol: [README.es.md](README.es.md)

Pipeline de migração de dados entre um sistema legado (com inconsistências,
duplicatas, campos faltantes) e um data store moderno, sem perder informação e
sem duplicá-la se o processo for executado duas vezes.

**Stack:** Python (ETL) · SQLAlchemy 2.0 · PostgreSQL · FastAPI + WebSocket (dashboard) · HTML/JS estático (frontend)

> Nota sobre a stack original: a proposta inicial usava Supabase como
> destino e Supabase Realtime + React para o dashboard. Este repositório usa
> **PostgreSQL puro** (via SQLAlchemy) — veja
> [docs/DECISIONS.md](docs/DECISIONS.md#adr-001-postgresql-en-vez-de-supabase)
> — e substitui o Supabase Realtime por `LISTEN/NOTIFY` do Postgres +
> WebSockets do FastAPI, com um frontend HTML/JS sem build step (veja
> [ADR-008](docs/DECISIONS.md#adr-008-frontend-estatico-sin-build-step-en-vez-de-un-spa-de-react)).

## Status do projeto

✅ **Fase 1** (Sprints 0-2): setup, seed de dados sujos, extração +
validação com Pydantic, carga idempotente com upsert por chave natural.

✅ **Fase 2, Sprint 3**: worker de retentativas com backoff exponencial
(`legacy_sync/etl/retry_worker.py`) + API FastAPI mínima com o endpoint de
retentativa forçada.

✅ **Fase 2, Sprint 4**: dashboard em tempo real — triggers do Postgres
(`LISTEN/NOTIFY`) + listener `asyncpg` + WebSocket (`legacy_sync/api/`), e
um frontend estático (`legacy_sync/static/`) que se atualiza sozinho, sem
recarregar a página, quando outro processo (`migrate`/`retry`) altera algo.

✅ **Fase 2, Sprint 5**: checkpointing — `legacy-sync migrate --resume-last`
retoma uma execução interrompida (`kill -9` no meio do caminho) logo após
o último registro confirmado, sem reler todo o legado do zero.

✅ **Fase 2, Sprint 6** (parcial): migrations versionadas com Alembic,
scheduler real (`legacy-sync worker`), imagem Docker única e
`docker-compose.prod.yml` para rodar API + worker + Postgres juntos —
verificado de ponta a ponta. **O deploy real em um provedor gerenciado e o
vídeo demo continuam pendentes**: exigem contas/credenciais próprias e
gravação manual, respectivamente — veja [docs/DEPLOY.md](docs/DEPLOY.md).

Veja [docs/ROADMAP.md](docs/ROADMAP.md) para o detalhamento sprint a sprint.

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — como o pipeline é estruturado e por quê.
- [docs/ROADMAP.md](docs/ROADMAP.md) — sprints concluídas e pendentes, com critérios de aceite.
- [docs/DECISIONS.md](docs/DECISIONS.md) — decisões de design (ADRs) e as alternativas descartadas.
- [docs/DEPLOY.md](docs/DEPLOY.md) — como rodar a stack em contêineres e levá-la a um provedor gerenciado.

> Observação: os documentos em `docs/` estão em espanhol.

## Quickstart

### 1. Subir o Postgres

```bash
docker compose up -d
cp .env.example .env
```

(Se você não tiver Docker disponível, qualquer Postgres 14+ serve — basta ajustar
`DATABASE_URL` no `.env`.)

### 2. Instalar as dependências

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Criar as tabelas, gerar dados sujos e migrar

```bash
python -m legacy_sync init-db
python -m legacy_sync seed --count 2000
python -m legacy_sync migrate
python -m legacy_sync status
```

### 4. Verificar a idempotência (a demo central do projeto)

```bash
python -m legacy_sync migrate   # rodar de novo
python -m legacy_sync status    # migrated_customers não deve crescer
```

### 5. Testar a fila de retentativas (Sprint 3)

```bash
python -m legacy_sync migrate --simulate-load-failures   # força falhas aleatórias de carga
python -m legacy_sync status                              # ver itens na retry_queue
python -m legacy_sync retry                               # processa a fila com backoff
python -m legacy_sync status                              # os bem-sucedidos já estão em migrated_customers
```

### 6. Subir o dashboard em tempo real (Sprint 4)

```bash
uvicorn legacy_sync.api.app:app --reload
```

Abra http://localhost:8000 — você verá os cards de resumo, a fila de
retentativas (com o botão "Reintentar ahora") e as falhas de validação. Deixe-o
aberto e, em outro terminal, execute:

```bash
python -m legacy_sync migrate --simulate-load-failures
python -m legacy_sync retry
```

Os contadores se atualizam sozinhos — a API recebe o aviso via
`LISTEN/NOTIFY` do Postgres a partir do processo da CLI (um processo
completamente diferente) e o repassa por WebSocket, sem polling.

Endpoints REST expostos: `GET /migrations/summary`,
`GET /migrations/validation-failures`, `GET /retry-queue`,
`POST /retry-queue/{id}/retry`, `WS /migrations/live`.

### 7. Simular um crash e retomar a migração (Sprint 5)

```bash
python -m legacy_sync seed --count 5000
python -m legacy_sync migrate &   # rodar em background
sleep 1 && kill -9 %1             # simular um crash no meio do caminho
python -m legacy_sync runs        # ver a execução que ficou "running" e seu run_id
python -m legacy_sync migrate --resume-last   # retoma logo após o último confirmado
python -m legacy_sync runs        # agora "completed"
```

### 8. Rodar o scheduler de retentativas (Sprint 6)

```bash
python -m legacy_sync worker --interval 30   # loop em foreground, Ctrl+C para parar
python -m legacy_sync worker --once          # uma única passada, para chamar via cron
```

### 9. Rodar tudo em contêineres (Sprint 6)

```bash
docker compose -f docker-compose.prod.yml up --build
docker compose -f docker-compose.prod.yml run --rm api legacy-sync seed --count 2000
docker compose -f docker-compose.prod.yml run --rm api legacy-sync migrate
```

Postgres + API (executa `alembic upgrade head` sozinha ao iniciar) + worker,
os três em contêineres separados. Detalhes e como levar a stack a um
provedor gerenciado em [docs/DEPLOY.md](docs/DEPLOY.md).

### 10. Rodar os testes

```bash
pytest -q
```

Os testes usam SQLite em memória (mesma camada de models/ETL, branch de
dialeto diferente no upsert) para não depender de uma instância de Postgres
no CI. `tests/test_load_idempotent.py` é o teste que comprova exatamente o
cenário "rodar a migração duas vezes não duplica nada".

## Estrutura do repositório

```
legacy_sync/
  models/        # SQLAlchemy: legado, destino, logs, fila de retentativas, checkpoints
  schemas.py     # Pydantic: validação estrita por registro
  realtime.py    # NOTIFY_CHANNEL + helper de DSN para asyncpg
  init_db.py     # aplica as migrations do Alembic (init-db / init-db --reset)
  etl/
    extract.py      # lê o legado (suporta after_legacy_id para resume)
    validate.py     # isola erros por registro (não quebra o batch)
    checksum.py     # detecta se um registro realmente mudou
    load.py         # upsert idempotente por chave natural
    pipeline.py      # orquestra extract -> validate -> load, gerencia checkpoints
    retry_worker.py  # processa a retry_queue com backoff exponencial (Sprint 3)
    checkpoint.py    # persistência do ponto de progresso de uma execução (Sprint 5)
  api/
    app.py                # FastAPI: dashboard + retentativa forçada (Sprint 3-4)
    listener.py           # LISTEN do Postgres via asyncpg (Sprint 4)
    connection_manager.py # registro de WebSockets conectados (Sprint 4)
  static/        # frontend HTML/JS sem build step (Sprint 4)
  seed.py        # gera dados "sujos" de teste
  cli.py         # comandos: init-db, seed, migrate, retry, worker, runs, status
alembic/         # migrations versionadas do schema (Sprint 6)
Dockerfile                 # imagem única: API, worker ou CLI conforme o comando (Sprint 6)
docker-compose.prod.yml    # postgres + api + worker juntos (Sprint 6)
tests/           # inclui o teste de idempotência (rodar 2x, 0 duplicatas)
docs/            # arquitetura, roadmap, decisões, deploy
```
