#!/usr/bin/env bash
# element-skin Webhook 测试一键部署脚本（Debian 系）
# 用法：sudo bash setup.sh
# 前置：本仓库已同步到 /opt/element-skin-webhook-test
set -euo pipefail

DOMAIN_SKIN="${DOMAIN_SKIN:-skin.your-domain.com}"
DOMAIN_HOOKS="${DOMAIN_HOOKS:-hooks.your-domain.com}"
ROOT_DIR="/opt/element-skin-webhook-test"
RECEIVER_DIR="$ROOT_DIR/receiver"

echo "==> 1/5 检查依赖"
command -v docker >/dev/null || { echo "缺少 docker，请先安装"; exit 1; }
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }

echo "==> 2/5 部署 element-skin demo 站（已有部署可跳过：export SKIP_DEMO=1）"
if [ "${SKIP_DEMO:-0}" = "1" ]; then
  echo "跳过 demo 站部署（使用已有 element-skin 部署）"
elif [ ! -d /opt/element-skin ]; then
  git clone https://github.com/water2004/element-skin.git /opt/element-skin
  cd /opt/element-skin
  cp .env.example .env
  echo "请编辑 /opt/element-skin/.env 后重新运行本脚本"
  exit 0
else
  cd /opt/element-skin
  docker compose up -d
fi

echo "==> 3/5 安装接收端依赖"
cd "$RECEIVER_DIR"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# element-skin-sdk 未发布到 PyPI，必须本地安装
if [ -d /opt/element-skin/python-sdk ]; then
  .venv/bin/pip install -e /opt/element-skin/python-sdk
else
  echo "错误：缺少 /opt/element-skin/python-sdk，请先同步 element-skin 的 python-sdk 目录"
  exit 1
fi
.venv/bin/pip install -r requirements.txt

echo "==> 4/5 准备接收端配置"
if [ ! -f "$RECEIVER_DIR/config.json" ]; then
  cp "$RECEIVER_DIR/config.example.json" "$RECEIVER_DIR/config.json"
  echo "请编辑 $RECEIVER_DIR/config.json 填入 signing_secret 后重新运行本脚本"
  exit 0
fi

echo "==> 5/5 安装 systemd 服务 + Nginx"
cp "$ROOT_DIR/deploy/receiver.service" /etc/systemd/system/element-skin-receiver.service
systemctl daemon-reload
systemctl enable --now element-skin-receiver

cp "$ROOT_DIR/deploy/nginx.conf" /etc/nginx/sites-available/element-skin-webhook-test
# 共用域名场景：替换 union-test.gpa.ac.cn（若用独立 hooks 域名，改用 DOMAIN_HOOKS 并调整配置）
sed -i "s/union-test\.gpa\.ac\.cn/$DOMAIN_SKIN/g" /etc/nginx/sites-available/element-skin-webhook-test
ln -sf /etc/nginx/sites-available/element-skin-webhook-test /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "==> 完成"
echo "demo 站:   https://$DOMAIN_SKIN"
echo "接收端:    https://$DOMAIN_SKIN/wh"
echo "下一步: 运行 scripts/ 测试（先 create_apps.py 创建应用）"
