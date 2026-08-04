#!/usr/bin/env sh
set -eu
cd /app

mkdir -p /app/data/media /app/data/profiles

# 配置落在 data 卷里，避免 compose 挂载缺失文件时变成目录
export CREATORHUB_CONFIG_PATH="${CREATORHUB_CONFIG_PATH:-/app/data/config.yaml}"
if [ ! -f "$CREATORHUB_CONFIG_PATH" ]; then
  cp /app/config.example.yaml "$CREATORHUB_CONFIG_PATH"
  echo "[CreatorHub] 已生成 $CREATORHUB_CONFIG_PATH"
fi

HOST="${CREATORHUB_HOST:-0.0.0.0}"
PORT="${CREATORHUB_PORT:-8000}"

echo "[CreatorHub] Docker 模式：扫码登录已关闭，请使用 Cookie 粘贴登录"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
