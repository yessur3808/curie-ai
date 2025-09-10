import psycopg2
from psycopg2.extras import DictCursor
from psycopg2 import OperationalError, DatabaseError, Error
from pymongo import MongoClient, errors as mongo_errors
import logging
from .config import PG_CONN_INFO, MONGODB_URI, MONGODB_DB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_pg_conn():
    try:
        conn = psycopg2.connect(**PG_CONN_INFO, cursor_factory=DictCursor)
        return conn
    except OperationalError as e:
        logger.error(f"Failed to connect to Postgres: {e}")
        raise RuntimeError(f"Failed to connect to Postgres: {e}")

try:
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
    mongo_db = mongo_client[MONGODB_DB]
    mongo_client.admin.command('ping')
except mongo_errors.PyMongoError as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise RuntimeError(f"Failed to connect to MongoDB: {e}")

def init_pg():
    try:
        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("""
                        CREATE EXTENSION IF NOT EXISTS "pgcrypto";
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            internal_id UUID UNIQUE DEFAULT gen_random_uuid(),
                            telegram_id TEXT,
                            slack_id TEXT,
                            whatsapp_id TEXT,
                            signal_id TEXT,
                            phone_number TEXT,
                            email TEXT,
                            secret_username TEXT NOT NULL,
                            is_master BOOLEAN NOT NULL DEFAULT FALSE,
                            roles TEXT[] DEFAULT ARRAY[]::TEXT[],
                            created_at TIMESTAMPTZ DEFAULT now(),
                            updated_at TIMESTAMPTZ DEFAULT now(),
                            updated_by TEXT NOT NULL
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS conversation_memory (
                            id SERIAL PRIMARY KEY,
                            user_internal_id UUID NOT NULL,
                            timestamp TIMESTAMPTZ NOT NULL,
                            role TEXT NOT NULL,
                            message TEXT NOT NULL,
                            FOREIGN KEY (user_internal_id) REFERENCES users(internal_id) ON DELETE CASCADE
                        );
                    """)
                    conn.commit()
                    logger.info("Postgres tables initialized successfully.")
                except DatabaseError as e:
                    conn.rollback()
                    logger.error(f"Error initializing Postgres tables: {e}")
                    raise RuntimeError(f"Error initializing Postgres tables: {e}")
    except Error as e:
        logger.error(f"Error in init_pg: {e}")
        raise RuntimeError(f"Error in init_pg: {e}")

def init_mongo():
    try:
        mongo_db.research_memory.create_index([('topic', 1)])
        mongo_db.research_memory.create_index([('user_id', 1)])
        logger.info("MongoDB indexes created successfully.")
    except mongo_errors.PyMongoError as e:
        logger.error(f"Error creating MongoDB indexes: {e}")
        raise RuntimeError(f"Error creating MongoDB indexes: {e}")

def init_databases():
    try:
        init_pg()
    except Exception as e:
        logger.error(f"Failed to initialize Postgres DB: {e}")
        raise RuntimeError(f"Failed to initialize Postgres DB: {e}")
    try:
        init_mongo()
    except Exception as e:
        logger.error(f"Failed to initialize MongoDB: {e}")
        raise RuntimeError(f"Failed to initialize MongoDB: {e}")