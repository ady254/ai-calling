from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

connect_args = {}
# If connecting to an external database like Neon, require SSL via connect_args
if "localhost" not in settings.DATABASE_URL and "127.0.0.1" not in settings.DATABASE_URL and "@postgres:" not in settings.DATABASE_URL:
    connect_args["ssl"] = "require"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Disable SQL logging in production for performance/security
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args=connect_args,
)


AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)