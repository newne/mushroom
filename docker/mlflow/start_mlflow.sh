#!/usr/bin/env sh
set -e

# ---------- 1) 读 Docker secret 并去除 CRLF/换行 ----------
if [ -f /run/secrets/mlflow_db_password ]; then
  DB_PW_RAW="$(cat /run/secrets/mlflow_db_password || true)"
else
  echo "[FATAL] Secret /run/secrets/mlflow_db_password not found" >&2
  exit 1
fi

DB_PW="$(printf '%s' "$DB_PW_RAW" | tr -d '\r\n')"

# 调试输出（长度安全，不泄露内容）
printf '[debug] len(raw)=%s len(trim)=%s\n' \
  "$(printf '%s' "$DB_PW_RAW" | wc -c)" \
  "$(printf '%s' "$DB_PW" | wc -c)"

if [ -z "$DB_PW" ]; then
  echo "[FATAL] Secret mlflow_db_password is empty after trim." >&2
  exit 1
fi

# ---------- 2) URL 编码（处理 @:/?#& 等特殊字符） ----------
py='import os,urllib.parse;print(urllib.parse.quote(os.environ["PW"], safe=""))'
DB_PW_ENC="$(PW="$DB_PW" python -c "$py" 2>/dev/null || true)"
[ -n "$DB_PW_ENC" ] || DB_PW_ENC="$DB_PW"  # 容器肯定有 Python；兜底以防万一

# ---------- 3) 组装数据库后端 URI（全部在容器内展开） ----------
# 依赖以下环境变量（来自 compose env）：POSTGRES_USER/POSTGRES_HOST/POSTGRES_PORT/POSTGRES_DB
MLFLOW_BACKEND_STORE_URI="postgresql+psycopg2://${POSTGRES_USER}:${DB_PW_ENC}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
export MLFLOW_BACKEND_STORE_URI

echo "[mlflow] Using backend store: ${MLFLOW_BACKEND_STORE_URI}"

# ---------- 4) 启动 MLflow（无 --threads；保留 --workers） ----------
exec mlflow server \
  --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
  --registry-store-uri "$MLFLOW_BACKEND_STORE_URI" \
  --serve-artifacts \
  --artifacts-destination "$MLFLOW_ARTIFACTS_DESTINATION" \
  --host "$MLFLOW_HOST" \
  --port "${MLFLOW_PORT:-5000}" \
  --uvicorn-opts "--workers 3 --timeout-keep-alive 120 --proxy-headers --forwarded-allow-ips='*'"
