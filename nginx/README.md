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

Ces limites de bordure amortissent les pics ; la limitation applicative
basée sur Redis (Exigence 26.4) reste le second rideau côté backend.
