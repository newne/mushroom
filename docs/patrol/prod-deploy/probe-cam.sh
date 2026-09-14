#!/bin/bash
# 只读盘点：采图服务接口 + 旧系统调用者 + 服务日志。不含运动指令。
echo "===== 1. 采图服务 OpenAPI ====="
curl -s -m 10 http://127.0.0.1:7003/openapi.json -o /tmp/oapi.json -w "http=%{http_code} bytes=%{size_download}\n" 2>&1
python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/oapi.json'))
except Exception as exc:
    print('  parse fail:', exc)
    raise SystemExit
print('  title:', d.get('info', {}).get('title'), '| version:', d.get('info', {}).get('version'))
for path, ops in d.get('paths', {}).items():
    for meth, op in ops.items():
        ps = op.get('parameters', [])
        print(f"  {meth.upper():5} {path}")
        for p in ps:
            sch = p.get('schema', {}) or {}
            print(f"          - {p.get('name')}  in={p.get('in')}  "
                  f"{'必需' if p.get('required') else '可选'}  "
                  f"type={sch.get('type')} default={sch.get('default')!r}")
        if not ps:
            print("          (无显式 query 参数)")
PY

echo
echo "===== 2. 谁在按小时调用 7003 ====="
echo "--- root crontab ---"
crontab -l 2>&1 | grep -vE '^\s*#' | grep -v '^\s*$' | head -20
echo "--- 其他用户 crontab ---"
for u in sysadmin mushroom ubuntu algorithm; do
  out=$(crontab -u "$u" -l 2>/dev/null | grep -vE '^\s*#' | grep -v '^\s*$')
  [ -n "$out" ] && { echo "[$u]"; echo "$out" | head -15; }
done
echo "--- systemd timers ---"
systemctl list-timers --all --no-pager 2>/dev/null | head -15
echo "--- 含 capture/camera 的进程 ---"
ps -ef | grep -iE 'capture|camera|sweep|mogu' | grep -v grep | head -15

echo
echo "===== 3. 采图服务最近日志 ====="
docker logs --tail 30 xcloud-capture-1 2>&1 | tail -30

echo
echo "===== 4. 服务容器与工作目录 ====="
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}' 2>&1 | head -15
echo "--- 容器内工作目录 ---"
docker exec xcloud-capture-1 sh -c 'pwd; ls -la; echo "--- config ---"; head -c 600 XCloudSDKTest_config.ini 2>/dev/null' 2>&1 | head -40
