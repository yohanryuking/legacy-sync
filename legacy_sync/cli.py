"""CLI de operacion del pipeline: init-db, seed, migrate, status.

    python -m legacy_sync init-db
    python -m legacy_sync seed --count 2000
    python -m legacy_sync migrate
    python -m legacy_sync migrate --simulate-load-failures   # ejercita retry_queue
    python -m legacy_sync migrate --resume-last               # retoma una corrida interrumpida
    python -m legacy_sync runs                                 # ver corridas y su punto de avance
    python -m legacy_sync retry                                 # procesa retry_queue una vez
    python -m legacy_sync worker --interval 30                  # la procesa en loop (Sprint 6)
    python -m legacy_sync status
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from legacy_sync.config import get_settings
from legacy_sync.db import SessionLocal
from legacy_sync.etl.pipeline import run_pipeline
from legacy_sync.etl.retry_worker import RetryRunResult, process_retry_queue
from legacy_sync.init_db import drop_db, init_db
from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import (
    MigratedCustomer,
    MigrationCheckpoint,
    MigrationLog,
    RetryQueueItem,
)
from legacy_sync.seed import generate_legacy_data

app = typer.Typer(help="Pipeline de migracion legado -> destino.")
console = Console()


@app.command("init-db")
def init_db_command(reset: bool = typer.Option(False, help="Elimina y recrea todas las tablas.")) -> None:
    """Crea las tablas de legado y destino en la DB configurada."""
    if reset:
        drop_db()
        console.print("[yellow]Tablas eliminadas.[/yellow]")
    init_db()
    console.print("[green]Tablas creadas.[/green]")


@app.command("seed")
def seed_command(
    count: int = typer.Option(None, help="Cantidad de clientes base a generar."),
) -> None:
    """Genera datos sucios en legacy_customers."""
    settings = get_settings()
    n = count or settings.seed_record_count
    with SessionLocal() as session:
        inserted = generate_legacy_data(session, n)
    console.print(f"[green]{inserted}[/green] filas insertadas en legacy_customers (base: {n}).")


@app.command("migrate")
def migrate_command(
    simulate_load_failures: bool = typer.Option(
        False, "--simulate-load-failures", help="Fuerza fallos aleatorios de carga para probar la cola de reintentos."
    ),
    resume: str = typer.Option(
        None, "--resume", help="Retoma una corrida interrumpida por su run_id (ver `legacy-sync runs`)."
    ),
    resume_last: bool = typer.Option(
        False, "--resume-last", help="Retoma la corrida 'running' mas reciente, sin tener que pasar el run_id."
    ),
    checkpoint_every: int = typer.Option(
        200, "--checkpoint-every", help="Cada cuantos registros se confirma el punto de avance."
    ),
) -> None:
    """Corre la migracion completa: extract -> validate -> load. Idempotente.

    Sin --resume/--resume-last arranca una corrida nueva desde cero (el
    comportamiento de siempre). Si el proceso se interrumpe a mitad de
    camino, volver a correr con --resume-last retoma justo despues del
    ultimo registro confirmado, sin releer los ya procesados.
    """
    with SessionLocal() as session:
        result = run_pipeline(
            session,
            simulate_load_failures=simulate_load_failures,
            resume_run_id=resume,
            resume_last=resume_last,
            checkpoint_every=checkpoint_every,
        )
        session.commit()

    table = Table(title="Resultado de la migracion")
    table.add_column("Metrica")
    table.add_column("Valor", justify="right")
    table.add_row("Run ID", result.run_id)
    table.add_row("Total leidos", str(result.total))
    table.add_row("Validos", str(result.valid))
    table.add_row("Invalidos (a migration_logs)", str(result.invalid))
    table.add_row("Insertados", str(result.inserted))
    table.add_row("Actualizados", str(result.updated))
    table.add_row("Sin cambios", str(result.unchanged))
    table.add_row("Fallidos en carga (a retry_queue)", str(result.load_failed))
    console.print(table)


def _process_retry_queue_once() -> RetryRunResult:
    with SessionLocal() as session:
        result = process_retry_queue(session)
        session.commit()
    return result


@app.command("retry")
def retry_command() -> None:
    """Procesa retry_queue una vez: reintenta los items cuyo next_attempt_at ya paso."""
    result = _process_retry_queue_once()

    table = Table(title="Resultado de la cola de reintentos")
    table.add_column("Metrica")
    table.add_column("Valor", justify="right")
    table.add_row("Intentados", str(result.attempted))
    table.add_row("Exitosos", str(result.succeeded))
    table.add_row("Fallidos definitivamente", str(result.failed_permanently))
    table.add_row("Reprogramados (pendientes)", str(result.still_pending))
    console.print(table)


@app.command("worker")
def worker_command(
    interval: int = typer.Option(30, help="Segundos de espera entre pasadas de retry_queue."),
    once: bool = typer.Option(
        False, "--once", help="Corre una sola pasada y termina, en vez de quedar en loop (para invocar desde cron)."
    ),
) -> None:
    """Scheduler de Sprint 6: procesa retry_queue periodicamente.

    Es el "quien y cuando" que le faltaba a `legacy-sync retry` (que solo
    corre una pasada). `--once` sirve para invocarlo desde un cron externo
    (systemd timer, cron de Linux, un scheduled job de la plataforma de
    deploy); sin `--once` queda corriendo en foreground con un sleep entre
    pasadas -- pensado para un contenedor/servicio dedicado (ver
    docs/DEPLOY.md).
    """
    if once:
        result = _process_retry_queue_once()
        console.print(
            f"intentados={result.attempted} exitosos={result.succeeded} "
            f"fallidos={result.failed_permanently} reprogramados={result.still_pending}"
        )
        return

    console.print(f"[cyan]Worker de retry_queue corriendo cada {interval}s. Ctrl+C para detener.[/cyan]")
    try:
        while True:
            result = _process_retry_queue_once()
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            console.print(
                f"[{timestamp}] intentados={result.attempted} exitosos={result.succeeded} "
                f"fallidos={result.failed_permanently} reprogramados={result.still_pending}"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker detenido.[/yellow]")


@app.command("runs")
def runs_command(limit: int = typer.Option(10, help="Cuantas corridas mostrar, mas reciente primero.")) -> None:
    """Lista corridas de migracion y su punto de avance (para elegir --resume)."""
    with SessionLocal() as session:
        stmt = select(MigrationCheckpoint).order_by(MigrationCheckpoint.started_at.desc()).limit(limit)
        checkpoints = session.execute(stmt).scalars().all()

    table = Table(title="Corridas de migracion")
    table.add_column("run_id")
    table.add_column("estado")
    table.add_column("ultimo legacy_id", justify="right")
    table.add_column("actualizado")
    for checkpoint in checkpoints:
        table.add_row(
            checkpoint.run_id,
            checkpoint.status,
            str(checkpoint.last_legacy_id_processed),
            str(checkpoint.updated_at),
        )
    console.print(table)


@app.command("status")
def status_command() -> None:
    """Muestra conteos actuales de cada tabla."""
    with SessionLocal() as session:
        counts = {
            "legacy_customers": session.scalar(select(func.count()).select_from(LegacyCustomer)),
            "migrated_customers": session.scalar(select(func.count()).select_from(MigratedCustomer)),
            "migration_logs": session.scalar(select(func.count()).select_from(MigrationLog)),
            "retry_queue (pending)": session.scalar(
                select(func.count()).select_from(RetryQueueItem).where(RetryQueueItem.status == "pending")
            ),
        }
    table = Table(title="Estado actual")
    table.add_column("Tabla")
    table.add_column("Filas", justify="right")
    for name, value in counts.items():
        table.add_row(name, str(value))
    console.print(table)


if __name__ == "__main__":
    app()
