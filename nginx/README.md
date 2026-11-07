# Nginx — bordure PARTIPOLAI

Configuration de la bordure HTTPS (Exigences 26.1, 26.4, 26.5, 26.6).

## Fichiers

- `nginx.conf` — configuration principale : réglages de transport, zones de
  limitation de débit (`general`, `auth`, `chat`), amont `partipolia_web`.
- `conf.d/partipolia.conf` — serveur virtuel : redirection HTTP→HTTPS,
  terminaison TLS, en-têtes de sécurité, application des limites de débit et
  reverse proxy vers le service `web`.
- `certs/` — certificats TLS (`fullchain.pem`, `privkey.pem`). **Non committé**
  (voir `.gitignore` : `*.pem`, `*.key`). À fournir au déploiement.

## Certificats en développement

Générer un certificat auto-signé pour tester HTTPS localement :

```bash
mkdir -p nginx/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout nginx/certs/privkey.pem \
  -out nginx/certs/fullchain.pem \
  -subj "/CN=localhost"
```

## Certificats en production

Utiliser des certificats émis par une autorité (ex. Let's Encrypt / certbot)
et les monter dans `nginx/certs/` sous les noms `fullchain.pem` et
`privkey.pem`. Le bloc `location /.well-known/acme-challenge/` autorise le
renouvellement ACME en HTTP.

## Limites de débit de bordure

| Zone     | Débit    | Cible                              |
|----------|----------|------------------------------------|
| general  | 30 r/s   | trafic API + SSR                   |
| auth     | 5 r/s    | `/api/v1/auth/login`, `/register`  |
| chat     | 1 r/s    | `/api/v1/chat` (protection accrue) |

Zones par action (définies dans `conf.d/partipolia.conf`, clé = IP, comptées
uniquement pour POST/PUT/PATCH/DELETE ; la lecture reste sous `general`) :

| Zone     | Débit    | Rafale | Cible                                                        |
|----------|----------|--------|--------------------------------------------------------------|
| claims   | 6 r/min  | 3      | `/api/v1/claims…` (création, publication, commentaires…)     |
| supports | 30 r/min | 10     | `/api/v1/claims/{id}/support` (soutien / retrait)            |
| consult  | 6 r/min  | 3      | `/api/v1/consultations/{id}/responses`                       |
| scenario | 30 r/min | 10     | `/api/v1/national-simulations/saved…` (sauvegarde, partage)  |

Dépassement : `limit_req_status 429` puis `error_page 429 @rate_limited`, qui
renvoie un corps JSON et l'en-tête `Retry-After: 10` (secondes). Les en-têtes
de sécurité sont répétés dans ce `location` car un `add_header` local masque
ceux du serveur.

Taille des corps : `client_max_body_size 1m` sur le serveur HTTPS ; au-delà,
nginx répond 413 (Exigence 15.4). La valeur de 20 Mo de `nginx.conf` ne reste
qu'un plafond global pour l'ingestion de documents (non montée).

Ces limites de bordure amortissent les pics ; la limitation applicative
basée sur Redis (Exigence 26.4) reste le second rideau côté backend.

## Exécution non privilégiée (Exigence 15.6)

Le conteneur utilise `nginxinc/nginx-unprivileged` (UID 101). nginx écoute donc
sur `8080` (HTTP) et `8443` (HTTPS) ; Compose publie ces ports en `80` et `443`.
Le PID est écrit dans `/tmp/nginx.pid` et la directive `user` est absente.
La dérogation CSP `'unsafe-eval'` est consignée dans `docs/security/audit-2026-11-07.md`.
