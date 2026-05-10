from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def ensure_database_schema() -> None:
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    if "properties" not in inspector.get_table_names():
        return

    property_columns = {column["name"] for column in inspector.get_columns("properties")}
    missing_columns = {
        "status": "ALTER TABLE properties ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active'",
        "freshness_score": "ALTER TABLE properties ADD COLUMN freshness_score FLOAT NOT NULL DEFAULT 0.5",
        "fitness_score": "ALTER TABLE properties ADD COLUMN fitness_score FLOAT NOT NULL DEFAULT 0.5",
        "verification_status": "ALTER TABLE properties ADD COLUMN verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified'",
        "listing_url": "ALTER TABLE properties ADD COLUMN listing_url VARCHAR(500) NOT NULL DEFAULT ''",
        "photo_storage_folder": "ALTER TABLE properties ADD COLUMN photo_storage_folder VARCHAR(300) NOT NULL DEFAULT ''",
        "canonical_key": "ALTER TABLE properties ADD COLUMN canonical_key VARCHAR(200) NOT NULL DEFAULT ''",
        "last_seen_at": "ALTER TABLE properties ADD COLUMN last_seen_at DATETIME",
        "last_verified_at": "ALTER TABLE properties ADD COLUMN last_verified_at DATETIME",
    }

    with engine.begin() as connection:
        for column_name, ddl in missing_columns.items():
            if column_name not in property_columns:
                connection.execute(text(ddl))

        connection.execute(
            text(
                """
                UPDATE properties
                SET
                    status = COALESCE(NULLIF(status, ''), 'active'),
                    freshness_score = COALESCE(freshness_score, 0.5),
                    fitness_score = COALESCE(fitness_score, freshness_score, 0.5),
                    verification_status = COALESCE(NULLIF(verification_status, ''), 'unverified'),
                    listing_url = COALESCE(NULLIF(listing_url, ''), owner_contact, ''),
                    canonical_key = CASE
                        WHEN canonical_key IS NULL OR canonical_key = '' THEN lower(title || '|' || location || '|' || bhk || '|' || type)
                        ELSE canonical_key
                    END,
                    last_seen_at = COALESCE(last_seen_at, CURRENT_TIMESTAMP)
                """
            )
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
