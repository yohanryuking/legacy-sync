"""CLI de operacion del pipeline: init-db, seed, migrate, status.

    python -m legacy_sync init-db
    python -m legacy_sync seed --count 2000
    python -m legacy_sync migrate
    python -m legacy_sync migrate --simulate-load-failures   # ejercita retry_queue
    python -m legacy_sync status
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from legacy_sync.config import get_settings
from legacy_sync.db import SessionLocal
from legacy_sync.etl.pipeline import run_pipeline
from legacy_sync.init_db import drop_db, init_db
from legacy_sync.models.legacy import LegacyCustomer
from legacy_sync.models.target import MigratedCustomer, MigrationLog, RetryQueueItem
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
) -> None:
    """Corre la migracion completa: extract -> validate -> load. Idempotente."""
    with SessionLocal() as session:
        result = run_pipeline(session, simulate_load_failures=simulate_load_failures)
        session.commit()

    table = Table(title="Resultado de la migracion")
    table.add_column("Metrica")
    table.add_column("Valor", justify="right")
    table.add_row("Total leidos", str(result.total))
    table.add_row("Validos", str(result.valid))
    table.add_row("Invalidos (a migration_logs)", str(result.invalid))
    table.add_row("Insertados", str(result.inserted))
    table.add_row("Actualizados", str(result.updated))
    table.add_row("Sin cambios", str(result.unchanged))
    table.add_row("Fallidos en carga (a retry_queue)", str(result.load_failed))
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
