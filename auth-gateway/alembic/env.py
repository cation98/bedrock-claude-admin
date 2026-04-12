"""Alembic environment configuration.

Reads DATABASE_URL from the app's Settings (pydantic-settings)
so alembic.ini does not contain credentials.
All model modules are imported below to register their tables
with Base.metadata for --autogenerate support.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ---------------------------------------------------------------------------
# Alembic Config object — provides access to alembic.ini values.
# ---------------------------------------------------------------------------
config = context.config

# Set up Python loggers from the config file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Inject sqlalchemy.url from app settings (DATABASE_URL env var).
# This avoids hardcoding credentials in alembic.ini.
# ---------------------------------------------------------------------------
from app.core.config import get_settings  # noqa: E402

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# ---------------------------------------------------------------------------
# Import ALL models so that Base.metadata contains every table.
# These imports mirror what main.py does for create_all().
# ---------------------------------------------------------------------------
from app.core.database import Base  # noqa: E402, F401
from app.models.user import User, SecurityTemplate  # noqa: E402, F401
from app.models.session import TerminalSession  # noqa: E402, F401
from app.models.audit_log import AuditLog  # noqa: E402, F401
from app.models.two_factor_code import TwoFactorCode  # noqa: E402, F401
from app.models.file_share import SharedDataset, FileShareACL  # noqa: E402, F401
from app.models.token_usage import TokenUsageDaily, TokenUsageHourly  # noqa: E402, F401
from app.models.token_quota import TokenQuotaTemplate, TokenQuotaAssignment  # noqa: E402, F401
from app.models.prompt_audit import PromptAuditSummary, PromptAuditFlag, PromptAuditConversation  # noqa: E402, F401
from app.models.proxy import AllowedDomain, ProxyAccessLog  # noqa: E402, F401
from app.models.bot import UserBot  # noqa: E402, F401
from app.models.app import DeployedApp, AppACL, AppView, AppLike  # noqa: E402, F401
from app.models.skill import SharedSkill, SkillInstall  # noqa: E402, F401
from app.models.survey import SurveyTemplate, SurveyAssignment, SurveyResponse  # noqa: E402, F401
from app.models.file_governance import GovernedFile  # noqa: E402, F401
from app.models.file_audit import FileAuditLog  # noqa: E402, F401
from app.models.infra_policy import InfraTemplate  # noqa: E402, F401
from app.models.edit_session import EditSession  # noqa: E402, F401
# Phase 0 신규 모델 — autogenerate에 포함되도록 반드시 import
from app.models.announcement import Announcement  # noqa: E402, F401
from app.models.guide import Guide  # noqa: E402, F401
from app.models.moderation import ModerationViolation  # noqa: E402, F401
from app.services.sqlcipher_service import SQLCipherKey  # noqa: E402, F401
from app.models.ui_source_event import UiSourceEvent  # noqa: E402, F401

# The target metadata that Alembic uses for --autogenerate diffing.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL (no Engine needed).
    Calls to context.execute() emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
