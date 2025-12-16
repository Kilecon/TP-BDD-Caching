import psycopg2.pool
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# Pool pour le PRIMARY (écritures via HAProxy)
primary_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=20,
    host='localhost',
    port=5439,  # ✅ HAProxy frontend (pointe vers db-primary ou db-replica après failover)
    database='appdb',
    user='app',
    password='app_pwd'
)

# Pool pour la REPLICA (lectures directes)
replica_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=20,
    host='localhost',
    port=5433,  # ✅ Accès direct à db-replica
    database='appdb',
    user='app',
    password='app_pwd'
)

@contextmanager
def get_primary_conn():
    """Context manager pour obtenir une connexion au primary via HAProxy"""
    conn = None
    try:
        conn = primary_pool.getconn()
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"[PRIMARY ERROR] {e}")
        raise
    finally:
        if conn:
            primary_pool.putconn(conn)

@contextmanager
def get_replica_conn():
    """Context manager pour obtenir une connexion à la replica"""
    conn = None
    try:
        conn = replica_pool.getconn()
        yield conn
    except Exception as e:
        logger.error(f"[REPLICA ERROR] {e}")
        raise
    finally:
        if conn:
            replica_pool.putconn(conn)

def close_all_connections():
    """Ferme tous les pools de connexions"""
    primary_pool.closeall()
    replica_pool.closeall()
    logger.info("[DB] All connection pools closed")
