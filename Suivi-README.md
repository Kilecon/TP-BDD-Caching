# TP Docker — Réplication PostgreSQL, Cache Redis & Haute Disponibilité

# PARTIE A — Mise en place Docker (20 min)

## A2. Lancer les services

```bash
$ docker compose ps   
NAME                          IMAGE                       COMMAND                  SERVICE      CREATED         STATUS         PORTS
tp-bdd-caching-db-primary-1   bitnami/postgresql:latest   "/opt/bitnami/script…"   db-primary   4 seconds ago   Up 3 seconds   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
tp-bdd-caching-db-replica-1   bitnami/postgresql:latest   "/opt/bitnami/script…"   db-replica   3 seconds ago   Up 3 seconds   0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp
tp-bdd-caching-redis-1        redis:7                     "docker-entrypoint.s…"   redis        4 seconds ago   Up 3 seconds   0.0.0.0:6379->6379/tcp, [::]:6379->6379/tcp
```

# PARTIE B — Vérifier la réplication PostgreSQL (30 min)

## B1. Vérifier le rôle des bases

### Primary
```bash
$ docker exec -it tp-bdd-caching-db-primary-1  psql -U app -d appdb
Password for user app: 
psql (18.1)
Type "help" for help.

appdb=> SELECT pg_is_in_recovery();
 pg_is_in_recovery 
-------------------
 f
(1 row)

appdb=>
```
➡️ Résultat attendu : `false` OK

### Replica

```bash
$ docker exec -it tp-bdd-caching-db-replica-1 psql -U app -d appdb
Password for user app: 
psql (18.1)
Type "help" for help.

appdb=> SELECT pg_is_in_recovery();
 pg_is_in_recovery 
-------------------
 t
(1 row)
```
➡️ Résultat attendu : `true` OK

## B2. Tester la réplication

Sur le **primary** :

```bash
$ docker exec -it tp-bdd-caching-db-primary-1  psql -U app -d appdb
Password for user app: 
psql (18.1)
Type "help" for help.

appdb=> CREATE TABLE products(
appdb(>   id SERIAL PRIMARY KEY,
appdb(>   name TEXT NOT NULL,
appdb(>   price_cents INT NOT NULL,
appdb(>   updated_at TIMESTAMP DEFAULT NOW()
appdb(> );
 products(name, price_cents)
VALUES ('Keyboard', 4CREATE TABLE
appdb=>
appdb=> INSERT INTO products(name, price_cents)
appdb-> VALUES ('Keyboard', 4999);
INSERT 0 1
appdb=>
```
Sur la **replica** :

```bash
docker exec -it tp-bdd-caching-db-replica-1 psql -U app -d appdb 
Password for user app: 
psql (18.1)
Type "help" for help.

appdb=> SELECT * FROM products;
 id |   name   | price_cents |         updated_at
----+----------+-------------+----------------------------
  1 | Keyboard |        4999 | 2025-12-16 12:54:13.900338
(1 row)
```

# PARTIE C — HAProxy comme point d’entrée DB (20 min)

```bash
$ docker compose restart haproxy
[+] Restarting 1/1
 ✔ Container tp-bdd-caching-haproxy-1  Started    
```

---  # j'en suis là

# PARTIE D — API : lectures, écritures et cache Redis (90 min)
D1. Architecture de l'API
L'API FastAPI implémente le pattern cache-aside avec Redis et utilise la réplication PostgreSQL pour distribuer la charge :

Writes (PUT/POST) → PostgreSQL primary (port 5432)

Reads (GET) → PostgreSQL replica (port 5433) avec cache Redis

Cache → Redis (port 6379, TTL 60s)

D2. Implémenter le cache Redis
Règles
Clé : product:{id}

TTL : 60 secondes (compromis optimal pour un catalogue produit)

Peut être augmenté à 120s si les données sont très stables

Réduit à 30s pour des données plus volatiles (prix dynamiques, stock)

Pattern cache-aside :

Tentative de lecture Redis

Si cache miss → lecture DB replica

Mise en cache du résultat avec TTL

Test : 1ère lecture (CACHE MISS)

```powershell
PS> Invoke-RestMethod -Uri "http://localhost:8000/products/1" -Method Get
source  data
------  ----
replica @{id=1; name=Keyboard; price_cents=4999; updated_at=16/12/2025 13:50:58}
```

Logs API :

```powershell
2025-12-16 14:54:10,933 - cache - INFO - [CACHE MISS] product:1
2025-12-16 14:54:10,942 - cache - INFO - [CACHE SET] product:1 with TTL 60s
INFO:     127.0.0.1:49844 - "GET /products/1 HTTP/1.1" 200 OK
```
➡️ Résultat : source: "replica" + cache rempli avec TTL 60s ✅

Test : 2ème lecture (CACHE HIT)
```powershell
PS> Invoke-RestMethod -Uri "http://localhost:8000/products/1" -Method Get

source data
------ ----
cache  @{id=1; name=Keyboard; price_cents=4999; updated_at=16/12/2025 13:50:58}
```
Logs API :

```powershell
2025-12-16 14:54:12,105 - cache - INFO - [CACHE HIT] product:1
INFO:     127.0.0.1:49845 - "GET /products/1 HTTP/1.1" 200 OK
```
➡️ Résultat : source: "cache" + aucune requête SQL ✅

## D3. Invalidation du cache
Lors d'un PUT /products/:id :

Mise à jour sur le primary PostgreSQL

Suppression de la clé Redis product:{id}

Test : Modification avec invalidation
```powershell
PS> $body = '{"name": "Updated Product", "price_cents": 9999}'
PS> Invoke-RestMethod -Uri "http://localhost:8000/products/1" -Method Put -Body $body -ContentType "application/json"

message                      data                                                                            note
-------                      ----                                                                            ----
Product updated successfully @{id=1; name=Updated Product; price_cents=9999; updated_at=16/12/2025 13:54:13} Next GET may…
```
Logs API :

```powershell
2025-12-16 14:54:13,077 - cache - INFO - [CACHE INVALIDATION] product:1 - deleted: 1
INFO:     127.0.0.1:49846 - "PUT /products/1 HTTP/1.1" 200 OK
```
➡️ Résultat : deleted: 1 confirme que la clé cache a bien été supprimée ✅

Test : Lecture immédiate après modification
```powershell
PS> Invoke-RestMethod -Uri "http://localhost:8000/products/1" -Method Get

source  data
------  ----
replica @{id=1; name=Updated Product; price_cents=9999; updated_at=16/12/2025 13:54:13}
```
Logs API :

```powershell
2025-12-16 14:54:15,145 - cache - INFO - [CACHE MISS] product:1
2025-12-16 14:54:15,149 - cache - INFO - [CACHE SET] product:1 with TTL 60s
INFO:     127.0.0.1:49847 - "GET /products/1 HTTP/1.1" 200 OK
```
➡️ Résultat : source: "replica" avec la nouvelle valeur 9999, cache invalidé puis recréé ✅

## D4. Expérience de cohérence
Test : Latence de réplication et cache

```powershell
PS> $testBody = '{"name": "Gaming Mouse", "price_cents": 5999}'
PS> Invoke-RestMethod -Uri "http://localhost:8000/test-consistency/1" -Method Post -Body $testBody -ContentType "application/json"

updated_value             : @{name=Gaming Mouse; price_cents=5999}
replica_value_immediately : @{id=1; name=Gaming Mouse; price_cents=5999; updated_at=16/12/2025 13:55:32}
replica_value_after_200ms : @{id=1; name=Gaming Mouse; price_cents=5999; updated_at=16/12/2025 13:55:32}
cached_value              : @{id=1; name=Gaming Mouse; price_cents=5999; updated_at=16/12/2025 13:55:32}
analysis                  : @{immediate_replication_lag=False; explanation=Replication was fast (< 1ms)}
```
Logs API :

```powershell
2025-12-16 14:55:32,775 - cache - INFO - [CACHE INVALIDATION] product:1 - deleted: 0
2025-12-16 14:55:32,981 - cache - INFO - [CACHE MISS] product:1
2025-12-16 14:55:32,982 - cache - INFO - [CACHE SET] product:1 with TTL 60s
INFO:     127.0.0.1:55882 - "POST /test-consistency/1 HTTP/1.1" 200 OK
```
➡️ Observation : Dans cet environnement local, la réplication est quasi-instantanée (< 1ms) ✅

❓ Question : Pourquoi peut-on lire une ancienne valeur ?
Deux facteurs créent une fenêtre d'incohérence potentielle :

1. Latence de réplication PostgreSQL (5-50ms typique en production)
PostgreSQL utilise la streaming replication asynchrone :

```text
Primary                          Replica
   │                                │
t=0ms: UPDATE price = 5999          │
   │                                │
t=2ms: Write WAL log                │
   │                                │
t=5ms: Send WAL ─────────────────▶ |
   │                                │
   │                          t=10ms: Apply WAL
   │                                │
   ▼                                ▼
   ```
Fenêtre d'incohérence : Entre t=0ms et t=10ms, une lecture sur la replica retourne encore l'ancienne valeur alors que le primary est déjà à jour.

2. Cache Redis sans invalidation
Sans l'invalidation du cache (DELETE de la clé), Redis servirait la valeur périmée pendant toute la durée du TTL (60s).

Avec invalidation correcte (implémentée dans l'API) :

Le cache est vidé immédiatement après le PUT

L'incohérence est limitée au seul lag de réplication (5-50ms)

Dans nos tests locaux : < 1ms, donc invisible

➡️ Solutions pour la production
Scénario	Solution
Lectures critiques post-écriture	Lire depuis le primary (session sticky / read-your-writes)
Lectures tolérantes	Accepter le lag de 5-50ms sur la replica
Élimination totale du lag	Utiliser la réplication synchrone (⚠️ pénalité performance)
Notre implémentation avec invalidation cache limite efficacement l'incohérence au seul lag de réplication réseau, négligeable en environnement local. ✅
---

# PARTIE E — Résilience : pannes contrôlées (30 min)

## E1. Panne Redis

```bash
docker compose stop redis
[+] Stopping 1/1
 ✔ Container tp-bdd-caching-redis-1  Stopped   
```

➡️ L’API doit continuer à fonctionner (sans cache).
Cela fonctionne mais attend le timeout de l'appel à redis. Non tolerable en production.
---

## E2. Panne de la replica

```bash
docker compose stop db-replica
[+] Stopping 1/1
 ✔ Container tp-bdd-caching-db-replica-1  Stopped 
```

➡️ Choisissez :
- fallback vers primary
- ou erreur explicite
Fallback en dégradé sur primary mais et log 

logs avec les deux docker down :

```powershell
025-12-16 15:15:33,068 - cache - ERROR - [REDIS ERROR] Timeout connecting to server
2025-12-16 15:15:33,068 - database - ERROR - [REPLICA ERROR] could not receive data from server: Software caused connection abort (0x00002745/10053)

2025-12-16 15:15:33,068 - main - ERROR - [REPLICA DOWN] could not receive data from server: Software caused connection abort (0x00002745/10053)
 - Fallback to primary
2025-12-16 15:15:55,195 - cache - ERROR - [CACHE SET FAILED] Timeout connecting to server
INFO:     127.0.0.1:65112 - "GET /products/1 HTTP/1.1" 200 OK
```
---

# PARTIE F — Haute Disponibilité PostgreSQL (60 min)

## F1. Test : arrêt du primary

```bash
docker compose stop db-primary
[+] Stopping 1/1
 ✔ Container tp-bdd-caching-db-primary-1  Stopped    
```

➡️ Les écritures échouent  
➡️ Conclusion : réplication ≠ HA

---

## F2. Promotion de la replica

```bash
docker exec -it db-replica pg_ctl promote -D /bitnami/postgresql/data
```

```sql
SELECT pg_is_in_recovery();
```

➡️ Résultat attendu : `false`

```powershell
docker exec -it tp-bdd-caching-db-replica-1 psql -U app -d appdb
Password for user app: 
psql (18.1)
Type "help" for help.

appdb=> SELECT pg_is_in_recovery();
 pg_is_in_recovery 
-------------------
 f
(1 row)
```
---

## F3. Bascule HAProxy

Modifier `haproxy.cfg` :

```cfg
backend pg_primary
  option tcp-check
  tcp-check connect
  server primary db-replica:5432 check
```

```bash
docker compose restart haproxy
[+] Restarting 1/1
 ✔ Container tp-bdd-caching-haproxy-1  Started  
```
---

## F4. Test de continuité

Relancer une écriture via l’API.

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/products/1" -Method Put -Body $body -ContentType "application/json"

message                      data                                                                        note
-------                      ----                                                                        ----
Product updated successfully @{id=1; name=New Product; price_cents=1999; updated_at=16/12/2025 14:45:48} Next GET may show stale data due to replication lag
```

➡️ Le service doit refonctionner sans modifier l’API.

---

## 📝 Questions finales (rapport)

1. Différence entre réplication et haute disponibilité ?
La réplication et la haute disponibilité sont deux concepts complémentaires mais distincts que nous avons pu observer concrètement dans ce TP.

La réplication PostgreSQL que nous avons mise en place consiste à copier automatiquement les données du serveur primary vers un ou plusieurs serveurs replica via le mécanisme de streaming replication. Concrètement, chaque modification effectuée sur le primary (INSERT, UPDATE, DELETE) génère des enregistrements WAL (Write-Ahead Log) qui sont transmis en continu aux replicas. Ces derniers appliquent ces modifications pour maintenir une copie identique des données. L'objectif principal de la réplication est double : d'une part améliorer les performances en distribuant la charge de lecture sur plusieurs serveurs, d'autre part assurer une redondance des données pour limiter le risque de perte. Dans notre TP, nous avons pu constater que les lectures GET passent par la replica sur le port 5433, ce qui décharge le primary et permet de scaler horizontalement les capacités de lecture. Nous avons également vérifié en partie B2 que les données insérées sur le primary apparaissent quasi-instantanément sur la replica, confirmant le bon fonctionnement de la réplication.

Cependant, la réplication seule ne garantit absolument pas la continuité du service. C'est ce que nous avons démontré en partie F1 lorsque nous avons arrêté le serveur primary avec la commande docker compose stop db-primary. Immédiatement, toutes les tentatives d'écriture PUT ou POST ont échoué avec une erreur de connexion refusée, car les replicas PostgreSQL en mode recovery sont en lecture seule et ne peuvent pas accepter d'écritures. Les lectures ont continué de fonctionner normalement via la replica, mais l'application était en mode dégradé, incapable de traiter les modifications de données. Cette situation peut durer des minutes voire des heures selon le temps nécessaire pour qu'un administrateur intervienne.

La haute disponibilité, en revanche, est une architecture complète qui vise à garantir la continuité de service même en cas de panne d'un composant critique. Elle repose sur plusieurs piliers : la détection automatique des pannes via des mécanismes de heartbeat et de health checks, l'élection automatique d'un nouveau serveur primary parmi les replicas disponibles, la promotion automatique de ce serveur (équivalent de notre pg_ctl promote manuel), et la reconfiguration automatique de l'infrastructure (HAProxy, DNS, etc.) pour router le trafic vers le nouveau primary. Dans notre TP, toutes ces étapes ont été réalisées manuellement en parties F2 et F3 : nous avons dû exécuter manuellement la commande de promotion, éditer le fichier haproxy.cfg pour changer la cible de db-primary à db-replica, puis redémarrer HAProxy. En production avec une solution de haute disponibilité comme Patroni, tout ce processus serait automatisé et prendrait entre 10 et 30 secondes au lieu de plusieurs minutes d'intervention humaine.

La différence fondamentale est donc que la réplication est un mécanisme de copie de données passif, tandis que la haute disponibilité est une orchestration active qui inclut la réplication mais ajoute l'intelligence nécessaire pour réagir automatiquement aux pannes. Une architecture peut avoir de la réplication sans haute disponibilité (notre cas dans le TP), mais l'inverse n'est pas possible car la HA nécessite des replicas sur lesquels basculer. En termes de garanties, la réplication améliore la durabilité des données (RPO proche de zéro) et les performances, tandis que la haute disponibilité améliore le temps de récupération (RTO) en éliminant le besoin d'intervention manuelle.

Un autre aspect important est la prévention du split-brain, un scénario catastrophique où deux serveurs se croient simultanément primary suite à une partition réseau. La réplication simple n'a aucun mécanisme pour gérer cela. Les solutions de haute disponibilité utilisent des systèmes de consensus distribué comme etcd ou Consul pour s'assurer qu'un seul nœud à la fois peut être élu primary, même en cas de problèmes réseau complexes.

2. Qu’est-ce qui est manuel ici ? Automatique ?

Notre architecture présente un mélange intéressant d'opérations automatiques et manuelles, révélant les limites d'une infrastructure sans orchestration complète.

Du côté des mécanismes automatiques qui fonctionnent sans intervention, nous avons d'abord la réplication des données elle-même. Une fois configurée via les variables d'environnement POSTGRESQL_REPLICATION_MODE dans le docker-compose, la réplication streaming fonctionne de manière totalement transparente. Chaque écriture sur le primary génère des WAL qui sont automatiquement transmis et appliqués sur la replica. Nous l'avons constaté en partie B2 où l'insertion de la ligne Keyboard sur le primary est immédiatement visible sur la replica sans aucune action de notre part. Ce mécanisme continue de fonctionner 24h/24 sans supervision tant que les deux serveurs sont opérationnels.

Le cache Redis avec son pattern cache-aside fonctionne également de manière automatique. Lors d'un GET sur un produit, l'API tente d'abord une lecture dans Redis. Si la clé existe, elle retourne immédiatement la valeur avec une latence de l'ordre de la milliseconde. Si la clé n'existe pas (cache miss), l'API interroge automatiquement la replica PostgreSQL, récupère les données, puis les stocke dans Redis avec un TTL de 60 secondes avant de retourner la réponse. Ce cycle lecture-miss-database-cache fonctionne sans intervention et permet d'atteindre des taux de hit supérieurs à 80 pourcent sur les données fréquemment consultées.

L'invalidation du cache après une modification est également automatisée dans notre code Python. Chaque fois qu'un PUT modifie un produit, le code exécute automatiquement un redis.delete sur la clé correspondante immédiatement après avoir commité la transaction en base. Nous avons pu observer dans les logs l'enchainement systématique UPDATE SQL suivi de CACHE INVALIDATION deleted 1, puis lors du GET suivant un CACHE MISS suivi d'un CACHE SET. Cette orchestration garantit que les données périmées ne restent pas dans le cache après modification.

Les mécanismes de résilience que nous avons implémentés sont également automatiques. Lorsque Redis est arrêté en partie E1, l'API détecte automatiquement l'échec de connexion via une exception, log un message REDIS DOWN Fallback to DB, et continue de fonctionner en interrogeant directement la base de données. De même, lorsque la replica est arrêtée en partie E2, le code catch l'erreur psycopg2.OperationalError, log REPLICA DOWN Fallback to primary, et redirige automatiquement les lectures vers le serveur primary. Ces fallbacks dégradés permettent de maintenir le service même en cas de panne partielle.

Notre endpoint de health check interroge automatiquement l'état des trois composants Redis, primary et replica à chaque appel GET /health. Il retourne un statut HTTP 503 Service Unavailable si un composant critique est down, permettant à des systèmes de monitoring externes de détecter les problèmes.

En revanche, de nombreuses opérations critiques restent totalement manuelles dans notre architecture. La détection des pannes est passive : il n'y a aucun système qui surveille activement l'état du primary et déclenche une alerte si celui-ci devient injoignable. Nous devons nous-mêmes constater l'échec des écritures pour détecter le problème. Il n'existe pas de mécanisme de heartbeat qui ping régulièrement le primary pour vérifier sa disponibilité.

La promotion d'une replica en primary est entièrement manuelle, comme nous l'avons fait en partie F2 avec la commande docker exec -it db-replica pg_ctl promote. Cette opération nécessite une connexion SSH au serveur, la connaissance de la commande exacte et du chemin du répertoire de données PostgreSQL. Dans un contexte de panne à 3 heures du matin, cette intervention manuelle introduit un délai incompressible de plusieurs minutes.

La reconfiguration de HAProxy est également un processus manuel complexe. Nous devons éditer le fichier haproxy.cfg, changer la ligne server primary db-primary:5432 en server primary db-replica:5432, sauvegarder le fichier, puis redémarrer le conteneur HAProxy avec docker compose restart. Pendant cette opération, il y a une brève interruption de service le temps que HAProxy redémarre et rétablisse les connexions. Dans un environnement production, on utiliserait plutôt l'API runtime de HAProxy ou un système de service discovery pour éviter le redémarrage.

Après un failover, la reconstruction d'une nouvelle replica à partir du nouveau primary est entièrement manuelle. Il faudrait soit redémarrer l'ancien primary en mode replica (en modifiant sa configuration pour pointer vers le nouveau primary), soit provisionner un nouveau serveur et initialiser la réplication depuis zéro avec pg_basebackup. Cette opération peut prendre des heures pour des bases de plusieurs téraoctets.

Le rollback en cas de faux positif (si l'ancien primary revient en ligne alors qu'on a déjà promu la replica) est également manuel et dangereux. Il faut détecter la situation de split-brain potentiel, arrêter l'ancien primary, le reconfigurer en replica, et le resynchroniser avec le nouveau primary. Sans procédure stricte, on risque des pertes de données ou des incohérences.

En production avec Patroni ou une solution équivalente, presque toutes ces opérations manuelles seraient automatisées. Patroni exécute un heartbeat toutes les 10 secondes vers etcd. Si le primary ne répond plus après trois tentatives (30 secondes), Patroni lance automatiquement une élection parmi les replicas disponibles. La replica avec le moins de lag est promue automatiquement en primary. HAProxy, configuré avec des health checks HTTP sur l'API REST de Patroni, détecte automatiquement le changement et bascule le trafic sans redémarrage. L'ensemble du failover prend 30 à 60 secondes sans intervention humaine.

3. Risques cache + réplication ?

La combinaison de cache Redis et de réplication PostgreSQL introduit plusieurs risques d'incohérence et de performance que nous avons pu observer partiellement dans le TP.

Le premier risque majeur concerne les lectures périmées ou stale reads. Ce phénomène découle directement de la nature asynchrone de la réplication PostgreSQL. Lorsqu'un client exécute un UPDATE sur le primary, la transaction est validée localement et retourne immédiatement un succès. Les enregistrements WAL correspondants sont ensuite transmis via le réseau aux replicas et appliqués de manière asynchrone. Ce délai, bien que généralement très court (5 à 50 millisecondes en production), crée une fenêtre temporelle pendant laquelle le primary et les replicas contiennent des données différentes. Si pendant cette fenêtre un autre client effectue une lecture sur la replica, il obtiendra l'ancienne valeur. Dans notre test de cohérence en partie D4, nous avons lancé l'endpoint test-consistency qui modifie un produit puis lit immédiatement depuis la replica. Dans notre environnement local avec des conteneurs sur la même machine, la réplication est si rapide (inférieure à 1 milliseconde) que nous avons obtenu immediate_replication_lag False, mais en production sur des serveurs distants ou avec une charge importante, ce lag peut atteindre plusieurs dizaines de millisecondes voire plusieurs secondes si le réseau est saturé ou si la replica a accumulé du retard.

Ce problème est amplifié par le cache Redis. Imaginons le scénario suivant : un produit est en cache avec un prix de 4999 centimes. Un utilisateur modifie le prix à 5999 via un PUT. Notre code invalide correctement le cache en exécutant redis.delete. Mais si immédiatement après, un autre utilisateur exécute un GET, notre API tente de lire depuis Redis (cache miss car on vient d'invalider), interroge la replica PostgreSQL, et à ce moment précis la replica n'a pas encore appliqué le WAL et retourne toujours 4999. Notre code met alors en cache cette valeur périmée pendant 60 secondes. Résultat : pendant une minute entière, tous les clients liront le mauvais prix même si la réplication s'est entre-temps terminée. C'est ce qu'on appelle une cache pollution : le cache amplifie temporellement l'incohérence initiale due au lag de réplication.

Un autre risque lié au cache est l'invalidation échouée. Nous l'avons observé en partie E1 lorsque Redis était arrêté. Quand un PUT modifie un produit, le code essaie d'invalider le cache mais Redis ne répond pas (timeout). L'écriture en base réussit mais l'invalidation échoue. Si Redis redémarre quelques secondes plus tard, le cache contient toujours l'ancienne valeur et la servira pendant toute la durée du TTL restant. Dans nos logs, on voit REDIS DOWN Cannot invalidate cache Timeout, mais le PUT retourne quand même un succès 200 OK au client. Ce dernier pense que sa modification est prise en compte, mais les lectures ultérieures montrent toujours l'ancienne valeur. Ce type d'incohérence silencieuse est particulièrement dangereux car difficile à détecter et à debugger.

Le phénomène de cache stampede constitue un risque de performance majeur. Il se produit lorsque le cache expire sur une clé très populaire (par exemple la page d'accueil ou un produit en promotion). À l'instant où le TTL de 60 secondes arrive à expiration, si mille clients simultanés tentent de lire ce produit, ils obtiennent tous un cache miss au même moment. Chacun lance alors une requête SQL vers la replica PostgreSQL. Résultat : au lieu d'avoir une requête par minute grâce au cache, on se retrouve avec mille requêtes en une fraction de seconde. La replica, même performante, ne peut pas absorber ce pic instantané. Les connexions s'accumulent, les temps de réponse explosent, certaines requêtes timeout. Pendant ce temps, d'autres produits subissent le même sort à mesure que leurs caches expirent. C'est un effet domino qui peut mener à une indisponibilité totale du service. Nous ne l'avons pas testé dans le TP car cela nécessiterait un outil de load testing, mais c'est un scénario classique en production.

Les timeouts en cascade représentent un autre danger. Lorsque Redis est surchargé ou redémarre, chaque tentative de lecture génère un timeout de plusieurs secondes. Si notre API a configuré un socket_timeout de 2 secondes et reçoit 100 requêtes par seconde, cela signifie que 200 threads ou processus sont bloqués en attente de timeout simultanément. Ces threads consomment de la mémoire et des ressources système. Si le nombre de workers de notre serveur uvicorn est limité (par exemple 10 workers), ils sont tous rapidement saturés en train d'attendre des timeouts Redis, et l'API devient incapable de traiter de nouvelles requêtes même si la base de données fonctionne parfaitement. Nous avons constaté cet effet en partie E1 où les logs montrent REDIS ERROR Timeout connecting to server suivi d'un délai notable avant Fallback to DB. Chaque requête subit ce délai, dégradant drastiquement l'expérience utilisateur.

Le risque d'incohérence multi-région est critique pour les applications globales. Imaginons une architecture avec un primary en Europe et des replicas en Amérique et en Asie, chacun avec son propre cache Redis local. Un utilisateur européen modifie un produit. L'invalidation du cache européen est immédiate. Mais la réplication vers l'Amérique prend 50 millisecondes due à la latence transatlantique, et vers l'Asie 100 millisecondes. Pendant ce temps, les caches américain et asiatique continuent de servir l'ancienne valeur. Pire encore, si un utilisateur américain lit le produit pendant cette fenêtre, son cache local est rempli avec la valeur périmée et la servira pendant 60 secondes supplémentaires même après que la réplication soit terminée. Résultat : des utilisateurs à travers le monde voient des versions différentes du même produit pendant une période prolongée.

Un dernier risque souvent sous-estimé est la corruption silencieuse des données en cache. Si un bug dans le code de sérialisation JSON transforme par erreur un prix de 9999 en 999, et que cette valeur corrompue est mise en cache, elle sera servie à tous les clients pendant 60 secondes avant que le cache n'expire. Contrairement à une corruption en base de données qui affecte une seule ligne modifiable, une corruption dans le cache se propage massivement et disparaît d'elle-même, rendant le debugging quasi impossible. Les logs ne montreront que des lectures normales avec CACHE HIT, sans trace de l'anomalie.

Pour mitiger ces risques, plusieurs stratégies existent. Pour les lectures critiques post-écriture (un utilisateur qui vient de modifier un produit et le relit immédiatement), on peut implémenter le pattern read-your-writes en lisant temporairement depuis le primary plutôt que la replica pendant quelques centaines de millisecondes après une modification. Pour le cache stampede, on peut utiliser la technique de probabilistic early expiration où le cache est rechargé aléatoirement juste avant expiration, ou implémenter un lock distribué avec Redis SETNX pour s'assurer qu'un seul client recharge le cache tandis que les autres attendent. Pour les invalidations échouées, on peut réduire le TTL à 30 secondes sur les données critiques, limitant ainsi la durée maximale d'incohérence. On peut aussi implémenter un système de retry avec backoff exponentiel sur l'invalidation, ou passer à un modèle write-through où on écrit simultanément dans le cache et la base. Pour l'incohérence multi-région, un système de pub-sub Redis permet de propager les invalidations instantanément à tous les caches globaux indépendamment de la réplication PostgreSQL.

4. Comment améliorer cette architecture en production ?
Pour transformer cette architecture de TP en un système production-ready capable de supporter une charge réelle et garantir une disponibilité élevée, plusieurs améliorations critiques sont nécessaires, organisées par ordre de priorité.

La première et plus critique amélioration est l'implémentation d'un système de haute disponibilité automatique pour PostgreSQL avec Patroni. Actuellement, notre failover manuel en parties F2-F3 nécessite plusieurs étapes humaines et prend plusieurs minutes. Patroni est un système d'orchestration qui transforme ce processus en un mécanisme entièrement automatique. Patroni s'installe sur chaque nœud PostgreSQL et communique via un store distribué comme etcd ou Consul. Chaque instance Patroni envoie un heartbeat toutes les 10 secondes vers etcd pour signaler qu'elle est vivante. Si le primary ne parvient pas à renouveler son heartbeat pendant 30 secondes (paramètre configurable), les autres nœuds Patroni détectent la panne et lancent automatiquement une élection. La replica avec le moins de lag de réplication (déterminé en comparant les positions LSN dans les WAL) est automatiquement promue en nouveau primary via l'équivalent de notre commande pg_ctl promote. Les autres replicas sont reconfigurées automatiquement pour répliquer depuis le nouveau primary. L'ensemble de ce processus prend entre 10 et 30 secondes sans aucune intervention humaine. Patroni expose également une API REST sur chaque nœud qui retourne le statut actuel (primary, replica, ou indisponible), permettant à HAProxy de router le trafic dynamiquement vers le nœud primary actif via des health checks HTTP plutôt que notre configuration statique actuelle. Cette amélioration transforme notre RTO (Recovery Time Objective) de plusieurs minutes à moins d'une minute.

La deuxième amélioration essentielle est la haute disponibilité pour Redis via Redis Sentinel ou Redis Cluster. Actuellement, si Redis tombe en panne comme en partie E1, notre API passe en mode dégradé avec tous les appels allant directement en base et subissant les timeouts Redis. Redis Sentinel est un système de surveillance et de failover pour Redis similaire conceptuellement à Patroni. On déploie au minimum trois instances Sentinel (toujours un nombre impair pour le quorum) qui surveillent un master Redis et une ou plusieurs replicas Redis. Si le master ne répond plus après un délai configuré (généralement 5 secondes), les Sentinels votent pour promouvoir automatiquement une replica en nouveau master et reconfigurent les clients. Notre application Python utiliserait alors la bibliothèque redis-py avec support Sentinel qui découvre automatiquement le master actuel et bascule transparemment en cas de failover. Pour des besoins de scalabilité plus avancés, Redis Cluster permet de partitionner les données sur plusieurs nœuds avec réplication et failover automatique, mais pour notre cas d'usage de catalogue produit, Sentinel est largement suffisant.

La troisième amélioration critique est l'ajout d'un connection pooler comme PgBouncer entre notre application et PostgreSQL. Actuellement, notre code Python crée des pools de connexions directement vers les bases de données avec un maximum de 20 connexions par pool. Si nous déployons 10 instances de notre API pour gérer la charge, nous avons potentiellement 200 connexions simultanées vers le primary (10 instances fois 20 connexions). PostgreSQL gère mal un grand nombre de connexions, avec des dégradations de performance au-delà de quelques centaines. PgBouncer est un proxy léger qui se place entre l'application et la base. Il accepte des milliers de connexions côté client mais maintient un petit pool de connexions réelles vers PostgreSQL (typiquement 25 à 50). Il utilise le mode transaction pooling où une connexion PostgreSQL réelle est allouée à un client uniquement pendant la durée d'une transaction, puis immédiatement restituée au pool pour servir un autre client. Cela permet de supporter 10000 clients simultanés avec seulement 50 connexions PostgreSQL réelles. PgBouncer ajoute une latence négligeable (moins d'une milliseconde) et réduit drastiquement la charge sur PostgreSQL. Dans notre architecture, on déploierait un conteneur PgBouncer devant HAProxy, et notre application se connecterait à PgBouncer plutôt que directement à HAProxy.

L'observabilité et le monitoring constituent la quatrième amélioration indispensable. Actuellement, nous n'avons aucune visibilité sur les métriques critiques de notre système. Il est essentiel de déployer une stack Prometheus pour collecter les métriques et Grafana pour les visualiser. Côté PostgreSQL, on utiliserait postgres_exporter qui expose des métriques comme le nombre de connexions actives, le taux de transactions par seconde, le lag de réplication en secondes ou en bytes, le cache hit ratio, le temps moyen des requêtes, etc. Côté Redis, redis_exporter fournit le nombre de clés, le hit rate du cache, la mémoire utilisée, le nombre de commandes par seconde. Côté application, on instrumenterait notre code Python avec prometheus_client pour exposer des métriques custom comme le nombre de cache hits versus misses, la distribution des temps de réponse par endpoint, le taux d'erreurs, le nombre de fallbacks vers le primary quand la replica est down. Toutes ces métriques seraient centralisées dans Prometheus avec des dashboards Grafana permettant de visualiser l'état du système en temps réel. Plus important encore, on configurerait des alertes dans Prometheus Alertmanager : alerte si le lag de réplication dépasse 100 millisecondes, alerte si le hit rate du cache tombe sous 70 pourcent, alerte si le taux d'erreur 5xx dépasse 1 pourcent, alerte si aucun primary n'est disponible. Ces alertes seraient envoyées vers PagerDuty ou Opsgenie pour notifier l'équipe on-call 24/7.

La cinquième amélioration concerne la résilience réseau et la distribution géographique avec un déploiement multi-AZ (Availability Zone). Actuellement, tous nos conteneurs tournent sur une seule machine. En production cloud (AWS, GCP, Azure), on déploierait le primary dans une zone de disponibilité (par exemple eu-west-1a), une première replica dans une autre zone (eu-west-1b), et une seconde replica dans une troisième zone (eu-west-1c). Chaque AZ est un datacenter physiquement séparé avec son propre réseau et alimentation électrique, mais dans la même région géographique avec une latence inférieure à 2 millisecondes. Cette configuration permet de survivre à la panne complète d'un datacenter (incendie, coupure électrique, etc.) tout en maintenant une latence acceptable pour la réplication synchrone. On configurerait PostgreSQL avec synchronous_commit remote_apply et synchronous_standby_names ANY 1 pour garantir qu'au moins une replica dans une AZ différente a appliqué chaque transaction avant de retourner un succès au client. Cela élimine tout risque de perte de données en cas de panne du primary tout en maintenant des performances acceptables.

La sixième amélioration est l'implémentation de mécanismes anti-cache-stampede dans notre code applicatif. Actuellement, quand un cache expire sur une clé populaire, tous les clients lancent simultanément une requête DB. On pourrait implémenter un lock distribué avec Redis SETNX : le premier client à détecter le cache miss acquiert un lock, recharge les données depuis la DB, met à jour le cache, puis libère le lock. Les autres clients détectent que le lock est pris, attendent quelques millisecondes, puis relisent le cache qui entre-temps a été rechargé par le premier client. Une autre approche est le probabilistic early expiration : au lieu d'attendre que le TTL expire complètement à 60 secondes, on recalcule le cache de manière probabiliste entre 50 et 60 secondes. Par exemple, si le TTL restant est de 5 secondes sur un TTL total de 60, on a une probabilité de 5/60 soit environ 8 pourcent de recharger le cache de manière anticipée. Cela étale dans le temps les rechargements de cache au lieu de les concentrer tous au moment de l'expiration, éliminant le pic de charge.

La septième amélioration concerne la sécurité et l'encryption. Actuellement, nos mots de passe PostgreSQL et nos données transitent en clair dans docker-compose.yml et sur le réseau. En production, on utiliserait des secrets managers comme HashiCorp Vault, AWS Secrets Manager, ou Kubernetes Secrets pour stocker les credentials. Les connexions PostgreSQL seraient sécurisées avec TLS/SSL en configurant ssl_mode require dans les paramètres de connexion. Le trafic de réplication entre primary et replicas serait également chiffré. On activerait l'encryption at rest dans PostgreSQL pour chiffrer les données sur disque. Redis supporterait également TLS pour les connexions clients. On implémenterait un WAF (Web Application Firewall) devant l'API pour protéger contre les injections SQL, XSS, et autres attaques courantes. On ajouterait de l'authentification sur notre API (JWT tokens, OAuth2) plutôt que de laisser tous les endpoints publics comme actuellement.

La huitième amélioration est l'optimisation des performances avec des indexes appropriés et du query tuning. Actuellement, notre table products a un index sur la clé primaire id, ce qui rend les requêtes SELECT WHERE id très rapides. Mais si notre application devait filtrer par name ou price_cents, ces requêtes feraient des full table scans. On créerait des index sur ces colonnes. On utiliserait EXPLAIN ANALYZE sur les requêtes fréquentes pour identifier les slow queries et optimiser les plans d'exécution. On configurerait pg_stat_statements pour tracker automatiquement les requêtes les plus coûteuses. Côté Redis, on passerait d'un TTL fixe de 60 secondes à des TTL adaptatifs : 120 secondes pour les produits peu modifiés, 30 secondes pour les produits fréquemment mis à jour. On pourrait également implémenter un cache L1 in-memory au niveau de l'application (un simple dictionnaire Python) pour les données ultra-fréquentes, évitant même l'appel réseau à Redis qui prend 1-2 millisecondes.

La neuvième amélioration serait l'architecture événementielle avec Kafka ou RabbitMQ pour découpler les composants. Plutôt que d'invalider le cache de manière synchrone dans le endpoint PUT (ce qui bloque le client si Redis est lent), on publierait un événement ProductUpdated dans Kafka. Un worker asynchrone consommerait cet événement et invaliderait le cache. Cela rend l'API plus réactive et permet de scaler indépendamment la partie écriture (API) et la partie invalidation (workers). On pourrait avoir plusieurs workers d'invalidation pour absorber les pics de modifications. Kafka garantit également la livraison des messages même en cas de panne temporaire des workers, évitant les invalidations perdues que nous avons observées en partie E1. On pourrait implémenter le pattern Event Sourcing où chaque modification génère un événement immutable stocké dans Kafka, permettant de reconstruire l'état de n'importe quel produit à n'importe quel moment dans le temps.

La dixième et dernière amélioration majeure serait l'implémentation de backups automatisés et de disaster recovery. Actuellement, si notre primary subit une corruption de données (bug applicatif qui DELETE toutes les lignes, ransomware, etc.), la réplication propage instantanément cette corruption aux replicas. Toutes nos données sont perdues. On mettrait en place des backups incrémentaux quotidiens avec pg_basebackup et archivage des WAL dans un stockage objet comme S3. On configurerait PostgreSQL avec archive_mode on et archive_command pour envoyer automatiquement chaque segment WAL vers S3 dès qu'il est rempli. Cela permet un RPO de quelques secondes : on peut restaurer la base à n'importe quel point dans le temps. On testerait régulièrement ces backups en restaurant sur un environnement de staging pour s'assurer qu'ils sont valides. On implémenterait également une réplication géographique asynchrone vers une autre région cloud (par exemple de eu-west vers us-east) pour survivre à la destruction complète d'une région. Redis serait également backupé avec RDB snapshots ou AOF (Append Only File).

Ces dix améliorations transformeraient notre architecture de TP fonctionnelle en un système production-ready capable de garantir 99.9 pourcent d'uptime (moins de 9 heures d'indisponibilité par an) voire 99.99 pourcent (moins d'une heure par an) avec les composants les plus avancés comme le multi-région. Le coût et la complexité augmentent évidemment proportionnellement, mais pour un service critique générant des revenus significatifs, ces investissements sont rapidement rentabilisés par la réduction des pertes liées aux pannes et la meilleure expérience utilisateur permise par les performances optimisées.
---

## 📊 Barème indicatif /20

- Docker & lancement : 3
- Réplication : 5
- Cache Redis : 5
- Résilience : 3
- Haute disponibilité : 4

---

## 🚀 Bonus
- Anti cache-stampede
- Failover automatique (Patroni)
- HA Redis (Sentinel)
