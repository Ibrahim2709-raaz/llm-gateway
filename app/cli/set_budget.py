import argparse
import asyncio
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.session import session_factory
from app.usage.service import set_budget_limit

PERIOD_PATTERN = re.compile(r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")


def positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal number") from error
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def valid_period(value: str) -> str:
    if PERIOD_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must use YYYY-MM format")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or update a monthly client budget.")
    parser.add_argument("--client-id", required=True, type=UUID)
    parser.add_argument("--period", required=True, type=valid_period)
    parser.add_argument("--limit-usd", required=True, type=positive_decimal)
    parser.add_argument("--alert-threshold-percent", type=int, default=80, choices=range(1, 101))
    return parser


async def set_budget(
    client_id: UUID,
    period: str,
    limit_usd: Decimal,
    alert_threshold_percent: int,
) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = session_factory(engine)
    try:
        async with factory.begin() as session:
            await set_budget_limit(
                session,
                client_id=client_id,
                period=period,
                limit_usd=limit_usd,
                alert_threshold_percent=alert_threshold_percent,
            )
    finally:
        await engine.dispose()


def main() -> None:
    arguments = build_parser().parse_args()
    asyncio.run(
        set_budget(
            arguments.client_id,
            arguments.period,
            arguments.limit_usd,
            arguments.alert_threshold_percent,
        )
    )
    print(
        f"Budget set: client={arguments.client_id} period={arguments.period} "
        f"limit_usd={arguments.limit_usd}"
    )


if __name__ == "__main__":
    main()
