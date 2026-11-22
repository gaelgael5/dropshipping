#!/usr/bin/env bash
# Déploie l'atelier d'annonces (branche dev) sur une machine de test.
#
# Usage (depuis le clone du dépôt sur la machine) :
#   ./dev-deploy.sh                      # déploiement normal
#   ./dev-deploy.sh --no-detach          # CI / débogage : sans le filet anti-raccrochage
#   ./dev-deploy.sh --exposer-seulement  # stack déjà en place : santé + déclaration seules
#
# Premier déploiement sur une machine partagée : choisir un port libre,
#   ATELIER_PUBLISHED_PORT=8110 ./dev-deploy.sh
# il est alors persisté dans .env et relu aux passages suivants.
#
# Déclaration à l'annuaire du portail (après la santé) : active seulement si
# PORTAL_TOKEN est dans l'environnement — code TOTP « <login>:<code> » obtenu par
# l'outil MCP `totp_code`, valable 30 s, jamais écrit dans .env ni dans le journal.
# Il expire bien avant la fin d'un build : on enchaîne en pratique ./dev-deploy.sh,
# puis un code frais et ./dev-deploy.sh --exposer-seulement.
#
# Prérequis : git, docker (+ plugin compose v2), curl, openssl, et l'accès réseau au
# dépôt GitHub — le script se met à jour depuis origin/dev avant tout ; il n'existe
# pas de mode « déployer ce qui est déjà là ».
set -euo pipefail

BRANCH="dev"
APP_SERVICE="app"
DB_SERVICE="db"
STEPS=10
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$REPO_DIR/deploy-logs"
PRUNE_STAMP="$LOG_DIR/.last-prune"
MIGRATIONS_DIR="$REPO_DIR/src/atelier/migrations"
cd "$REPO_DIR"

DETACH=1
EXPOSE_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-detach) DETACH=0 ;;
    --exposer-seulement) EXPOSE_ONLY=1 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $arg" >&2; exit 2 ;;
  esac
done

# --- 0. Survivre à la perte de la session -----------------------------------
# Le script arrête la stack : si le terminal tombe entre l'arrêt et la relance,
# SIGHUP le tuerait et laisserait la stack à terre. On ignore HUP et on journalise
# tout dans un fichier (`tee -p` continue d'écrire quand le terminal a disparu).
mkdir -p "$LOG_DIR"
if [[ "$DETACH" == 1 && -z "${DEV_DEPLOY_LOGGING:-}" ]]; then
  export DEV_DEPLOY_LOGGING=1
  DEV_DEPLOY_LOG="$LOG_DIR/deploy-$(date +%Y%m%d-%H%M%S).log"
  export DEV_DEPLOY_LOG
  trap '' HUP PIPE
  exec > >(tee -a -p "$DEV_DEPLOY_LOG") 2>&1
  echo "Journal du déploiement : $DEV_DEPLOY_LOG"
fi

step() { echo; echo "=== [$1/$STEPS] $2"; }
die()  { echo "ERREUR : $*" >&2; exit 1; }

# --- 1. Prérequis --------------------------------------------------------------
step 1 "Prérequis"
command -v git     >/dev/null || die "git introuvable — sudo apt-get install -y git"
command -v curl    >/dev/null || die "curl introuvable — sudo apt-get install -y curl"
command -v openssl >/dev/null || die "openssl introuvable — sudo apt-get install -y openssl"
command -v docker  >/dev/null || die "docker introuvable — https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 \
  || die "plugin docker compose v2 introuvable — sudo apt-get install -y docker-compose-plugin"
docker info >/dev/null 2>&1 \
  || die "le démon docker ne répond pas, ou droits insuffisants — sudo usermod -aG docker \$USER (puis reconnexion)"
echo "OK : compose $(docker compose version --short) / $(git --version)"

# --- 2. Mise à jour depuis le dépôt, puis ré-exécution ---------------------------
# Sans ré-exécution, on déploierait le code neuf avec un script, un compose et un
# Dockerfile périmés. DEV_DEPLOY_REEXEC évite de boucler.
step 2 "Mise à jour depuis origin/$BRANCH"
if [[ -z "${DEV_DEPLOY_REEXEC:-}" ]]; then
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || die "modifications locales sur la machine (git status) : aucune retouche à la main n'est admise, tout passe par le dépôt"
  before="$(git rev-parse HEAD)"
  git fetch --prune origin "$BRANCH"
  git checkout -q "$BRANCH"
  git merge --ff-only -q "origin/$BRANCH" \
    || die "la branche locale a divergé d'origin/$BRANCH — reclone la branche dev"
  after="$(git rev-parse HEAD)"
  if [[ "$before" != "$after" ]]; then
    echo "Mis à jour ${before:0:7} -> ${after:0:7} : relance avec la version récupérée"
    export DEV_DEPLOY_REEXEC=1
    exec "$REPO_DIR/dev-deploy.sh" "$@"
  fi
fi
echo "Déploiement de $(git log -1 --format='%h %s')"

# --- 3. Configuration : compléter .env sans écraser l'existant --------------------
# Les secrets sont générés ICI, sur la machine, et seulement s'ils manquent :
# régénérer le mot de passe de la base à chaque passage casserait la connexion à
# un volume de données déjà initialisé avec l'ancien.
step 3 "Configuration (.env)"
touch .env
chmod 600 .env
env_get() { grep "^$1=" .env | tail -1 | cut -d= -f2-; }
# Valeur absente de .env : prise dans l'environnement si elle y est, sinon le
# défaut ; elle est persistée et relue aux passages suivants.
env_default() { grep -q "^$1=" .env || echo "$1=${!1:-$2}" >> .env; }
env_secret()  { grep -q "^$1=" .env || echo "$1=$(openssl rand -hex 24)" >> .env; }
env_default COMPOSE_PROJECT_NAME dropshipping
env_default ATELIER_PUBLISHED_PORT 8000
env_secret  ATELIER_DB_PASSWORD
env_secret  ATELIER_CAPTURE_TOKEN
# Cible de la déclaration à l'annuaire (le jeton, lui, n'est jamais ici).
env_default EXPOSE_PORTAL_URL https://dev.yoops.org
env_default EXPOSE_WORKSPACE dropshipping
env_default EXPOSE_SERVICE atelier
PORT="$(env_get ATELIER_PUBLISHED_PORT)"
STACK="$(env_get COMPOSE_PROJECT_NAME)"
export COMPOSE_PROJECT_NAME="$STACK"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || die "ATELIER_PUBLISHED_PORT invalide dans .env : $PORT"
echo "Stack : $STACK — port publié : $PORT"

if [[ "$EXPOSE_ONLY" == 1 ]]; then
  echo "--exposer-seulement : étapes 4 à 7 sautées (stack supposée en place)"
else

# --- 4. Construction --------------------------------------------------------------
# Construire ici, depuis la copie fraîche du dépôt, est la vraie validation de
# l'image : jamais d'image réutilisée d'ailleurs.
step 4 "Construction de l'image"
docker compose build --pull

# --- 5. Arrêt (orphelins compris) puis contrôle du port ---------------------------
# Le port se sonde APRÈS l'arrêt : avant, on prendrait notre propre conteneur
# pour un conflit. Les volumes (base, images) ne sont jamais supprimés.
step 5 "Arrêt de la stack et contrôle du port"
docker compose down --remove-orphans
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  die "le port $PORT est occupé par un autre service après l'arrêt de la stack — change ATELIER_PUBLISHED_PORT dans .env (diagnostic : ss -ltnp 'sport = :$PORT')"
fi
echo "Port $PORT libre"

# --- 6. Lancement, attente d'un conteneur réellement en marche --------------------
# L'application applique ses migrations au démarrage et sort en erreur si elle
# échoue : un conteneur qui redémarre en boucle n'est pas « en marche ».
step 6 "Lancement"
docker compose up -d
state=""
for _ in $(seq 1 45); do
  state="$(docker compose ps --format '{{.State}}' "$APP_SERVICE" 2>/dev/null || true)"
  restarts="$(docker inspect -f '{{.RestartCount}}' "$(docker compose ps -q "$APP_SERVICE")" 2>/dev/null || echo 0)"
  [[ "$state" == "running" && "$restarts" == 0 ]] && break
  [[ "$restarts" != 0 ]] && die "le conteneur $APP_SERVICE redémarre en boucle — docker compose logs --tail=50 $APP_SERVICE"
  sleep 2
done
[[ "$state" == "running" ]] \
  || die "le conteneur $APP_SERVICE n'est pas en marche (état : ${state:-absent}) — docker compose logs --tail=50 $APP_SERVICE"
echo "Conteneur $APP_SERVICE en marche"

# --- 7. Schéma à jour : vérifier le RÉSULTAT, pas le code de retour ----------------
# Une migration peut « réussir » sans atteindre la dernière version (base tamponnée
# à la main) : on compare la version enregistrée au plus grand numéro livré.
step 7 "Vérification du schéma"
expected=0
for f in "$MIGRATIONS_DIR"/[0-9][0-9][0-9][0-9]_*.sql; do
  [[ -e "$f" ]] || continue
  n=$((10#$(basename "$f" | cut -c1-4)))
  (( n > expected )) && expected=$n
done
current=""
for _ in $(seq 1 30); do
  current="$(docker compose exec -T "$DB_SERVICE" psql -U atelier -d atelier -Atc \
    "SELECT coalesce(max(version), 0) FROM schema_migrations" 2>/dev/null || true)"
  [[ "$current" == "$expected" ]] && break
  sleep 2
done
[[ "$current" == "$expected" ]] \
  || die "schéma en retard : version ${current:-inconnue}, attendue $expected — docker compose logs --tail=50 $APP_SERVICE"
echo "Schéma à jour (version $expected)"
fi

# --- 8. Contrôle de santé, plafonné, sur le port réellement publié ----------------
step 8 "Contrôle de santé (http://127.0.0.1:$PORT/api/health)"
ok=0
for _ in $(seq 1 45); do
  if curl -fs --max-time 2 "http://127.0.0.1:$PORT/api/health" | grep -q '"reachable"'; then ok=1; break; fi
  sleep 2
done
[[ "$ok" == 1 ]] || die "pas de réponse saine en 90 s — docker compose logs --tail=100 $APP_SERVICE"
echo "Service sain : http://$(hostname):$PORT/"

# --- 9. Déclaration à l'annuaire du portail (best-effort) ---------------------------
# Après la santé : on ne publie pas une stack qui ne répond pas. Voie --me : le
# service est enregistré sous le workspace de l'appelant authentifié par son TOTP.
step 9 "Déclaration à l'annuaire"
if [[ -n "${PORTAL_TOKEN:-}" ]]; then
  # Le portail n'accepte qu'une IP du mesh (100.64.0.0/10) ou celle d'une machine
  # qu'il connaît — l'IP LAN est refusée (422). On préfère donc l'adresse mesh.
  ips="$(hostname -I 2>/dev/null)"
  TARGET_HOST="$(tr ' ' '\n' <<<"$ips" | grep -E '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.' | head -1 || true)"
  [[ -n "$TARGET_HOST" ]] || TARGET_HOST="$(awk '{print $1}' <<<"$ips")"
  bash "$REPO_DIR/scripts/declarer-exposition.sh" --me \
    --portal "$(env_get EXPOSE_PORTAL_URL)" \
    --workspace "$(env_get EXPOSE_WORKSPACE)" \
    --service "$(env_get EXPOSE_SERVICE)" \
    --target-host "$TARGET_HOST" \
    --target-port "$PORT" \
    --host "$(hostname)"
else
  echo "PORTAL_TOKEN absent : pas de déclaration (relancer avec un code TOTP et --exposer-seulement)"
fi

# --- 10. Purge disque hebdomadaire (ne fait jamais échouer le déploiement) ---------
# Chaque build laisse des couches détaggées que rien ne récupère. Portée limitée au
# cache de build et aux images détaggées ; jamais les volumes ni les images de base.
# Témoin horodaté : au plus une fois par semaine, sinon on finit par la désactiver.
step 10 "Purge disque (hebdomadaire)"
if [[ "$EXPOSE_ONLY" == 1 ]]; then
  echo "--exposer-seulement : pas de purge"
elif [[ ! -f "$PRUNE_STAMP" ]] || [[ -n "$(find "$PRUNE_STAMP" -mtime +6 2>/dev/null)" ]]; then
  {
    echo "Avant : $(df -h / | awk 'NR==2{print $4}') libres"
    docker builder prune -f --filter until=168h
    docker image prune -f
    echo "Après : $(df -h / | awk 'NR==2{print $4}') libres"
    touch "$PRUNE_STAMP"
  } || echo "AVERTISSEMENT : purge en échec (déploiement non affecté)"
else
  echo "Dernière purge il y a moins d'une semaine : rien à faire"
fi

echo
echo "Déploiement terminé."
