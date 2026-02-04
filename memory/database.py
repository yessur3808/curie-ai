import psycopg2
from psycopg2.extras import DictCursor
from psycopg2 import OperationalError, DatabaseError, Error
from pymongo import MongoClient, errors as mongo_errors
import logging
import threading
from .config import PG_CONN_INFO, MONGODB_URI, MONGODB_DB

logger = logging.getLogger(__name__)

# MongoDB client and database instances (lazy initialization)
_mongo_client = None
_mongo_db = None
_mongo_lock = threading.Lock()

def get_pg_conn():
    try:
        conn = psycopg2.connect(**PG_CONN_INFO, cursor_factory=DictCursor)
        return conn
    except OperationalError as e:
        logger.error(f"Failed to connect to Postgres: {e}")
        raise RuntimeError(f"Failed to connect to Postgres: {e}") from e

def _init_mongo_connection():
    """Initialize MongoDB connection. Called lazily on first access. Thread-safe."""
    global _mongo_client, _mongo_db
    
    with _mongo_lock:
        # Double-check pattern to avoid multiple initializations
        if _mongo_client is not None:
            return
        
        if not MONGODB_URI or not MONGODB_DB:
            raise RuntimeError(
                "MongoDB configuration is missing. Please set MONGODB_URI and MONGODB_DB environment variables."
            )
        
        try:
            _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
            _mongo_db = _mongo_client[MONGODB_DB]
            _mongo_client.admin.command('ping')
            logger.info("MongoDB connection established successfully.")
        except mongo_errors.PyMongoError as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise RuntimeError(f"Failed to connect to MongoDB: {e}") from e

class _MongoDBProxy:
    """Proxy class that lazily initializes MongoDB connection on first access."""
    def __getattr__(self, name):
        if _mongo_db is None:
            _init_mongo_connection()
        return getattr(_mongo_db, name)

class _MongoClientProxy:
    """Proxy class that lazily initializes MongoDB client on first access."""
    def __getattr__(self, name):
        if _mongo_client is None:
            _init_mongo_connection()
        return getattr(_mongo_client, name)

# Module-level instances that will initialize lazily
mongo_db = _MongoDBProxy()
mongo_client = _MongoClientProxy()

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
                    raise RuntimeError(f"Error initializing Postgres tables: {e}") from e
    except Error as e:
        logger.error(f"Error in init_pg: {e}")
        raise RuntimeError(f"Error in init_pg: {e}") from e

def init_mongo():
    try:
        mongo_db.research_memory.create_index([('topic', 1)])
        mongo_db.research_memory.create_index([('user_id', 1)])
        logger.info("MongoDB indexes created successfully.")
    except mongo_errors.PyMongoError as e:
        logger.error(f"Error creating MongoDB indexes: {e}")
        raise RuntimeError(f"Error creating MongoDB indexes: {e}") from e

def init_databases():
    try:
        init_pg()
    except Exception as e:
        logger.error(f"Failed to initialize Postgres DB: {e}")
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Failed to initialize Postgres DB: {e}") from e
    try:
        # Eagerly initialize MongoDB connection at startup to catch configuration
        # or connectivity issues early, rather than deferring until first use
        _init_mongo_connection()
        init_mongo()
    except Exception as e:
        logger.error(f"Failed to initialize MongoDB: {e}")
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Failed to initialize MongoDB: {e}") from e