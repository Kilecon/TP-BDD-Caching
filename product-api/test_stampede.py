import asyncio
import httpx
import time

async def concurrent_requests(url, num_requests):
    """Lance num_requests requêtes simultanées"""
    async with httpx.AsyncClient() as client:
        tasks = [client.get(url) for _ in range(num_requests)]
        start = time.time()
        responses = await asyncio.gather(*tasks)
        duration = time.time() - start
        
        print(f"✅ {num_requests} requêtes en {duration:.2f}s")
        print(f"📊 Temps moyen: {duration/num_requests*1000:.0f}ms par requête")
        
        return responses

async def test_stampede():
    url = "http://localhost:8000/products/1"
    
    # 1. Remplir le cache
    print("1️⃣ Remplissage initial du cache...")
    async with httpx.AsyncClient() as client:
        await client.get(url)
    
    # 2. Attendre que le cache expire
    print("2️⃣ Attente expiration cache (60s)...")
    await asyncio.sleep(61)
    
    # 3. Lancer 100 requêtes simultanées
    print("3️⃣ Lancement de 100 requêtes simultanées...")
    await concurrent_requests(url, 100)
    
    # Vérifier les logs - devrait voir un seul LOCK ACQUIRED

if __name__ == "__main__":
    asyncio.run(test_stampede())
