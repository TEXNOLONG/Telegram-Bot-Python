#!/usr/bin/env bash
# ============================================================
# LoadTest Pro — VPS Setup Script (Ubuntu 22.04 / 24.04)
# Автоматическая установка и запуск бота на VPS
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Запустите скрипт от root: sudo bash vps_setup.sh"

APP_DIR="/opt/loadtest-pro"
APP_USER="loadtest"
PYTHON_VER="3.11"

info "=== LoadTest Pro — установка на VPS ==="
info "Каталог: $APP_DIR | Пользователь: $APP_USER"

# ── 1. Обновление системы ──────────────────────────────────
info "Обновляем пакеты..."
apt-get update -qq
apt-get upgrade -y -qq

# ── 2. Зависимости ────────────────────────────────────────
info "Устанавливаем зависимости..."
apt-get install -y -qq \
    python${PYTHON_VER} \
    python${PYTHON_VER}-venv \
    python3-pip \
    python3-dev \
    postgresql \
    postgresql-contrib \
    libpq-dev \
    build-essential \
    git \
    curl \
    wget \
    ufw \
    fail2ban \
    supervisor \
    nginx \
    certbot \
    python3-certbot-nginx

# ── 3. Настройка PostgreSQL ───────────────────────────────
info "Настраиваем PostgreSQL..."
systemctl start postgresql
systemctl enable postgresql

DB_PASS=$(openssl rand -base64 20 | tr -dc 'a-zA-Z0-9' | head -c 24)
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'loadtest') THEN
    CREATE USER loadtest WITH PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;
CREATE DATABASE IF NOT EXISTS loadtest_db OWNER loadtest;
GRANT ALL PRIVILEGES ON DATABASE loadtest_db TO loadtest;
SQL
info "БД создана. Пароль: ${DB_PASS}"
DATABASE_URL="postgresql://loadtest:${DB_PASS}@localhost:5432/loadtest_db"

# ── 4. Системный пользователь ────────────────────────────
info "Создаём пользователя $APP_USER..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/bash -d "$APP_DIR" "$APP_USER"
fi

# ── 5. Клонирование / копирование кода ───────────────────
info "Копируем код в $APP_DIR..."
mkdir -p "$APP_DIR"
if [[ -f "./main.py" ]]; then
    # Запущено из папки с кодом — копируем
    cp -r . "$APP_DIR/"
elif [[ -n "${GIT_REPO:-}" ]]; then
    git clone "$GIT_REPO" "$APP_DIR"
else
    warn "Код не найден. Скопируйте файлы в $APP_DIR вручную, затем запустите:"
    warn "  sudo bash $APP_DIR/vps_setup.sh"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ── 6. Python venv + зависимости ─────────────────────────
info "Устанавливаем Python-пакеты..."
python${PYTHON_VER} -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
if [[ -f "$APP_DIR/requirements.txt" ]]; then
    "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
else
    "$APP_DIR/venv/bin/pip" install -q \
        aiogram==3.* \
        aiohttp \
        flask \
        flask-sqlalchemy \
        sqlalchemy \
        psycopg2-binary \
        gunicorn \
        python-dotenv
fi

# ── 7. .env файл ─────────────────────────────────────────
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    info "Создаём $ENV_FILE — ЗАПОЛНИТЕ ТОКЕНЫ!"
    cat > "$ENV_FILE" <<ENV
BOT_TOKEN=ВСТАВЬТЕ_ТОКЕН_БОТА
ADMIN_ID=ВСТАВЬТЕ_TELEGRAM_ID
CRYPTO_BOT_TOKEN=ВСТАВЬТЕ_CRYPTOBOT_TOKEN
DATABASE_URL=${DATABASE_URL}
SESSION_SECRET=$(openssl rand -hex 32)
REPLIT_DEV_DOMAIN=ВАШ_ДОМЕН_ИЛИ_IP
ENV
    chmod 600 "$ENV_FILE"
    chown "$APP_USER":"$APP_USER" "$ENV_FILE"
    warn "Отредактируйте $ENV_FILE и укажите BOT_TOKEN / ADMIN_ID / CRYPTO_BOT_TOKEN"
else
    info ".env уже существует, пропускаем."
fi

# ── 8. Supervisor (автозапуск) ────────────────────────────
info "Настраиваем supervisor..."
cat > /etc/supervisor/conf.d/loadtest-pro.conf <<SUPERVISOR
[program:loadtest-pro]
command=${APP_DIR}/venv/bin/python ${APP_DIR}/main.py
directory=${APP_DIR}
user=${APP_USER}
autostart=true
autorestart=true
startsecs=5
stopwaitsecs=30
stdout_logfile=/var/log/loadtest-pro/app.log
stderr_logfile=/var/log/loadtest-pro/error.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=5
stderr_logfile_maxbytes=50MB
environment=PATH="${APP_DIR}/venv/bin"
SUPERVISOR

mkdir -p /var/log/loadtest-pro
chown -R "$APP_USER":"$APP_USER" /var/log/loadtest-pro
supervisorctl reread
supervisorctl update

# ── 9. Nginx reverse proxy ────────────────────────────────
info "Настраиваем Nginx..."
SERVER_NAME="${SERVER_NAME:-$(curl -s ifconfig.me 2>/dev/null || echo 'localhost')}"
cat > /etc/nginx/sites-available/loadtest-pro <<NGINX
server {
    listen 80;
    server_name ${SERVER_NAME};

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        client_max_body_size 10M;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/loadtest-pro /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
systemctl enable nginx

# ── 10. Firewall ──────────────────────────────────────────
info "Настраиваем UFW файрволл..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ── 11. Fail2ban ──────────────────────────────────────────
info "Настраиваем Fail2ban..."
cat > /etc/fail2ban/jail.local <<F2B
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port    = ssh

[nginx-http-auth]
enabled = true
F2B
systemctl restart fail2ban
systemctl enable fail2ban

# ── 12. Системные лимиты для большого числа соединений ───
info "Настраиваем системные лимиты (ulimit / sysctl)..."
cat >> /etc/security/limits.conf <<LIMITS
${APP_USER} soft nofile 65536
${APP_USER} hard nofile 65536
root        soft nofile 65536
root        hard nofile 65536
LIMITS

cat >> /etc/sysctl.conf <<SYSCTL
# LoadTest Pro optimizations
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.core.netdev_max_backlog = 65535
fs.file-max = 200000
SYSCTL
sysctl -p > /dev/null 2>&1 || true

# ── 13. Запуск ────────────────────────────────────────────
info "Запускаем приложение..."
supervisorctl start loadtest-pro || true

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║      LoadTest Pro успешно установлен!            ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  IP сервера:  ${YELLOW}${SERVER_NAME}${NC}"
echo -e "  Веб-панель:  ${YELLOW}http://${SERVER_NAME}/${NC}"
echo -e "  .env файл:   ${YELLOW}${ENV_FILE}${NC}"
echo -e "  Логи:        ${YELLOW}/var/log/loadtest-pro/app.log${NC}"
echo ""
echo -e "${YELLOW}ВАЖНО: Отредактируйте $ENV_FILE и укажите токены!${NC}"
echo -e "Затем перезапустите: ${GREEN}supervisorctl restart loadtest-pro${NC}"
echo ""
