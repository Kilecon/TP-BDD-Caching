import redis
import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration avec résilience
redis_client = redis.Redis(
    host='localhost',
    port=6379,
    db=0,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    retry_on_timeout=True,
    health_check_interval=30
)

CACHE_TTL = 60  # 60 secondes - compromis fraîcheur/performance

def get_cached(key: str) -> Optional[dict]:
    """Récupère une valeur du cache avec gestion d'erreur"""
    try:
        cached = redis_client.get(key)
        if cached:
            logger.info(f"[CACHE HIT] {key}")
            return json.loads(cached)
        logger.info(f"[CACHE MISS] {key}")
        return None
    except redis.ConnectionError as e:
        logger.error(f"[REDIS DOWN] {e} - Fallback to DB")
        return None
    except Exception as e:
        logger.error(f"[REDIS ERROR] {e}")
        return None

def set_cached(key: str, value: dict, ttl: int = CACHE_TTL) -> bool:
    """Met en cache une valeur avec TTL"""
    try:
        redis_client.setex(key, ttl, json.dumps(value, default=str))
        logger.info(f"[CACHE SET] {key} with TTL {ttl}s")
        return True
    except redis.ConnectionError as e:
        logger.error(f"[REDIS DOWN] Cannot cache: {e}")
        return False
    except Exception as e:
        logger.error(f"[CACHE SET FAILED] {e}")
        return False

def invalidate_cache(key: str) -> bool:
    """Invalide une clé du cache"""
    try:
        deleted = redis_client.delete(key)
        logger.info(f"[CACHE INVALIDATION] {key} - deleted: {deleted}")
        return bool(deleted)
    except redis.ConnectionError as e:
        logger.error(f"[REDIS DOWN] Cannot invalidate: {e}")
        return False
    except Exception as e:
        logger.error(f"[CACHE INVALIDATION FAILED] {e}")
        return False

def ping_redis() -> bool:
    """Test de connexion Redis"""
    try:
        redis_client.ping()
        return True
    except Exception:
        return False
