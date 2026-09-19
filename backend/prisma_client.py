import os
import logging
from typing import Optional

log = logging.getLogger("gem.prisma")

try:
    from prisma import Prisma
    prisma_client: Optional[Prisma] = Prisma()
except Exception as e:
    prisma_client = None
    log.info(f"Prisma client not available or not generated ({e}). Run 'prisma generate' to enable Prisma ORM.")


async def connect_prisma() -> bool:
    """Connect to Supabase/Postgres using Prisma Client Python if available."""
    if prisma_client is None:
        return False
    try:
        if not prisma_client.is_connected():
            await prisma_client.connect()
            log.info("Prisma Client connected to PostgreSQL/Supabase successfully.")
        return True
    except Exception as e:
        log.warning(f"Prisma connection failed (falling back to pooled psycopg2): {e}")
        return False


async def disconnect_prisma():
    """Disconnect Prisma Client if connected."""
    if prisma_client and prisma_client.is_connected():
        try:
            await prisma_client.disconnect()
            log.info("Prisma Client disconnected.")
        except Exception:
            pass
