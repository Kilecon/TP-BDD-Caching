from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
import logging
import time
import psycopg2

from database import get_primary_conn, get_replica_conn, close_all_connections
from cache import get_cached, set_cached, invalidate_cache, ping_redis, CACHE_TTL
from models import (
    Product, ProductCreate, ProductUpdate, ProductResponse,
    ConsistencyTestResult, HealthStatus
)

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Product API - TP PostgreSQL + Redis")

# ============ PARTIE D2 : GET avec cache-aside ============
@app.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: int):
    """
    Récupère un produit avec pattern cache-aside:
    1. Tentative lecture Redis
    2. Cache miss → lecture replica
    3. Mise en cache
    """
    cache_key = f"product:{product_id}"
    
    # 1. Tentative lecture Redis
    cached = get_cached(cache_key)
    if cached:
        return ProductResponse(
            source="cache",
            data=Product(**cached)
        )
    
    # 2. Lecture sur la REPLICA
    product = None
    source = "replica"
    
    try:
        with get_replica_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, price_cents, updated_at FROM products WHERE id = %s",
                    (product_id,)
                )
                row = cur.fetchone()
                
                if not row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Product not found"
                    )
                
                product = {
                    "id": row[0],
                    "name": row[1],
                    "price_cents": row[2],
                    "updated_at": row[3]
                }
    except psycopg2.OperationalError as e:
        # E2: Panne replica - fallback vers primary
        logger.error(f"[REPLICA DOWN] {e} - Fallback to primary")
        
        try:
            with get_primary_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, name, price_cents, updated_at FROM products WHERE id = %s",
                        (product_id,)
                    )
                    row = cur.fetchone()
                    
                    if not row:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="Product not found"
                        )
                    
                    product = {
                        "id": row[0],
                        "name": row[1],
                        "price_cents": row[2],
                        "updated_at": row[3]
                    }
                    source = "primary (replica down)"
        except psycopg2.OperationalError as primary_err:
            logger.error(f"[PRIMARY DOWN] {primary_err}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable"
            )
        
        # E2: Option alternative - retourner 503 directement
        # raise HTTPException(
        #     status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        #     detail="Replica unavailable"
        # )
    
    # 3. Mise en cache avec TTL
    set_cached(cache_key, product, CACHE_TTL)
    
    return ProductResponse(
        source=source,
        data=Product(**product)
    )

# ============ PARTIE D3 : PUT avec invalidation cache ============
@app.put("/products/{product_id}")
async def update_product(product_id: int, product_update: ProductUpdate):
    """
    Met à jour un produit sur le primary et invalide le cache
    """
    try:
        # 1. Mise à jour sur le PRIMARY
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE products 
                       SET name = %s, price_cents = %s, updated_at = NOW() 
                       WHERE id = %s
                       RETURNING id, name, price_cents, updated_at""",
                    (product_update.name, product_update.price_cents, product_id)
                )
                row = cur.fetchone()
                
                if not row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Product not found"
                    )
                
                updated_product = {
                    "id": row[0],
                    "name": row[1],
                    "price_cents": row[2],
                    "updated_at": row[3]
                }
        
        # 2. Invalidation du cache Redis
        cache_key = f"product:{product_id}"
        invalidate_cache(cache_key)
        
        return {
            "message": "Product updated successfully",
            "data": Product(**updated_product),
            "note": "Next GET may show stale data due to replication lag"
        }
        
    except psycopg2.Error as e:
        logger.error(f"[UPDATE ERROR] {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update product: {str(e)}"
        )

# ============ PARTIE D4 : Endpoint pour tester l'incohérence ============
@app.post("/test-consistency/{product_id}", response_model=ConsistencyTestResult)
async def test_consistency(product_id: int, product_update: ProductUpdate):
    """
    Teste l'incohérence causée par la latence de réplication et le cache
    """
    results = {
        "updated_value": {
            "name": product_update.name,
            "price_cents": product_update.price_cents
        },
        "replica_value_immediately": None,
        "replica_value_after_200ms": None,
        "cached_value": None,
        "analysis": {}
    }
    
    try:
        # 1. Mise à jour sur primary
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE products SET name = %s, price_cents = %s, updated_at = NOW() WHERE id = %s",
                    (product_update.name, product_update.price_cents, product_id)
                )
        
        # 2. Invalidation cache
        cache_key = f"product:{product_id}"
        invalidate_cache(cache_key)
        
        # 3. Lecture IMMEDIATE sur replica
        with get_replica_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price_cents, updated_at FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
                if row:
                    results["replica_value_immediately"] = Product(
                        id=row[0], name=row[1], price_cents=row[2], updated_at=row[3]
                    )
        
        # 4. Attente 200ms puis nouvelle lecture
        time.sleep(0.2)
        
        with get_replica_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price_cents, updated_at FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
                if row:
                    results["replica_value_after_200ms"] = Product(
                        id=row[0], name=row[1], price_cents=row[2], updated_at=row[3]
                    )
        
        # 5. Test du cache (simulation d'un GET normal)
        cached = get_cached(cache_key)
        if not cached and results["replica_value_after_200ms"]:
            # Remplit le cache
            product_dict = results["replica_value_after_200ms"].dict()
            set_cached(cache_key, product_dict, CACHE_TTL)
            results["cached_value"] = results["replica_value_after_200ms"]
        elif cached:
            results["cached_value"] = Product(**cached)
        
        # Analyse
        immediate = results["replica_value_immediately"]
        is_consistent = (
            immediate and 
            immediate.price_cents == product_update.price_cents
        )
        
        results["analysis"] = {
            "immediate_replication_lag": not is_consistent,
            "explanation": (
                "Replication was fast (< 1ms)" if is_consistent
                else "Stale data detected - demonstrates replication lag"
            )
        }
        
        return ConsistencyTestResult(**results)
        
    except Exception as e:
        logger.error(f"[CONSISTENCY TEST ERROR] {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

# ============ Endpoints supplémentaires ============

@app.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(product: ProductCreate):
    """Crée un nouveau produit"""
    try:
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO products (name, price_cents, updated_at) 
                       VALUES (%s, %s, NOW()) 
                       RETURNING id, name, price_cents, updated_at""",
                    (product.name, product.price_cents)
                )
                row = cur.fetchone()
                
                created_product = {
                    "id": row[0],
                    "name": row[1],
                    "price_cents": row[2],
                    "updated_at": row[3]
                }
        
        return {
            "message": "Product created",
            "data": Product(**created_product)
        }
        
    except psycopg2.Error as e:
        logger.error(f"[CREATE ERROR] {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/products")
async def list_products():
    """Liste tous les produits (sans cache)"""
    try:
        with get_replica_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price_cents, updated_at FROM products ORDER BY id")
                rows = cur.fetchall()
                
                products = [
                    Product(id=row[0], name=row[1], price_cents=row[2], updated_at=row[3])
                    for row in rows
                ]
        
        return {"source": "replica", "data": products}
        
    except psycopg2.Error as e:
        logger.error(f"[LIST ERROR] {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

# ============ Health Check ============
@app.get("/health", response_model=HealthStatus)
async def health_check():
    """Vérifie l'état de tous les composants"""
    health = {
        "redis": "UNKNOWN",
        "primary": "UNKNOWN",
        "replica": "UNKNOWN"
    }
    
    # Test Redis
    if ping_redis():
        health["redis"] = "OK"
    else:
        health["redis"] = "DOWN"
    
    # Test Primary
    try:
        with get_primary_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_is_in_recovery()")
                is_in_recovery = cur.fetchone()[0]
                health["primary"] = "REPLICA MODE (!)" if is_in_recovery else "OK (PRIMARY)"
    except Exception as e:
        health["primary"] = f"DOWN: {str(e)}"
    
    # Test Replica
    try:
        with get_replica_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_is_in_recovery()")
                is_in_recovery = cur.fetchone()[0]
                health["replica"] = "OK (REPLICA)" if is_in_recovery else "PRIMARY MODE (promoted!)"
    except Exception as e:
        health["replica"] = f"DOWN: {str(e)}"
    
    is_healthy = (
        health["redis"] == "OK" and 
        "OK" in health["primary"] and 
        "OK" in health["replica"]
    )
    
    status_code = status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    
    return JSONResponse(
        status_code=status_code,
        content=health
    )

# ============ Lifecycle Events ============
@app.on_event("shutdown")
async def shutdown_event():
    """Fermeture propre des connexions"""
    logger.info("Shutting down...")
    close_all_connections()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
