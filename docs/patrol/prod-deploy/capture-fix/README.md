# 采图服务（Mushroom_CLI @ :7003）加固包

## 一、这份加固解决什么

服务在 `xcloudsdk_py_offline_20260120_175307` 这个 compose 项目里以容器形态跑，
容器内 `Xvfb :99` + `libXCloudSDK.so` 做取流截图。历史故障的特征是：

> **进程活着、`/healthz` 返回 200、而每一次采图都 500。**

排查后确认的四个结构性缺陷（与触发原因无关，缺陷本身成立）：

| # | 缺陷 | 证据 |
| --- | --- | --- |
| 1 | Xvfb 起不来时**只打一行警告就继续启动** → 服务进入 headless，`DISPLAY` 未设 | 故障响应里 `headless_mode: true, display: ""` 正是 `unset DISPLAY` 那条分支 |
| 2 | 就绪判定用 `[ -S socket ]`，**残留 socket 会骗过它** | 实测：close 后的 socket 文件仍满足 `-S`，但连接被拒 |
| 3 | Xvfb 是**无人监管**的后台进程，它死了 python 照跑 | 原入口 `Xvfb ... &` 之后直接 `exec python3`，无 supervisor |
| 4 | `/healthz` **不反映显示器**，docker healthcheck 拿它当探针等于没探 | `DOCKER.md` 自述："只表示进程存活（应该始终返回 200）" |

⇒ 结果是**没有任何信号能触发 `restart: unless-stopped`**，故障会一直躺着直到有人手工重启。

> 未确定的一项：最近一次故障的**具体触发点**（需要那个已被重建掉的容器才能确认）。
> 但这不影响加固的有效性——上面 4 条把**所有会走到"静默 headless"的路径**都封死了。

## 二、加固内容

| 文件 | 作用 |
| --- | --- |
| `capture-entrypoint.sh` | 覆盖镜像 `ENTRYPOINT`：真连接探测就绪 + **fail-closed** + 运行期互监管 |
| `capture-healthcheck.py` | 容器内健康检查：**X socket 真能连** + HTTP `/healthz`（故意不抓拍） |
| `docker-compose.override.yml` | compose 自动合并的覆盖层；也设 `TZ`/`init: true`/healthcheck |
| `capture-status.sh` | 宿主端体检脚本；`--fix` 执行重建 |

三条关键行为：

1. **fail-closed**：Xvfb 10 s 内没起来 → `exit 1`，交给 `restart` 策略重试。
   故障从"静默 500"变成"可见的 crash loop"。
2. **真就绪探测**：`connect()` 到 `/tmp/.X11-unix/X99` 成功才算就绪。
3. **互监管**：Xvfb 或 python 任一退出 → 整个容器退出重启。

## 三、部署

```bash
cd /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cp -a docker-compose.yml docker-compose.yml.bak.$(date +%Y%m%d%H%M)   # 先备份
# 把本目录 4 个文件拷进来（capture-status.sh 可选）
docker compose config >/dev/null && echo "override 解析 OK"            # 预检：只看合并结果
docker compose up -d                                                   # 不要带 -f
./capture-status.sh                                                    # 复查
```

⚠️ **两个必须注意的点**

- **不要**用 `docker compose -f docker-compose.yml up -d`——显式 `-f` 会跳过覆盖层。
- `capture-entrypoint.sh` / `capture-healthcheck.py` **必须先存在**再 `up`：Docker 会把
  不存在的 bind-mount 源创建成**目录**，挂进容器就是目录而非文件（本包部署顺序已保证这点）。

## 四、验证

```bash
# 1) 只在独立 display 上验证 Xvfb 引导（不启服务、不碰相机）
docker run --rm -e XVFB_DISPLAY=:95 -e CAPTURE_ENTRYPOINT_DRYRUN=1 \
  -v "$PWD/capture-entrypoint.sh:/app/capture-entrypoint.sh:ro" \
  --entrypoint /bin/sh xcloudsdk-py:0.1.0 /app/capture-entrypoint.sh
#    期望：✅ Xvfb 就绪 … 退出码 0

# 2) 验证 fail-closed：故意给坏的 Xvfb 参数，必须 exit 1
docker run --rm -e XVFB_SERVER_ARGS="-screen bogus" \
  -v "$PWD/capture-entrypoint.sh:/app/capture-entrypoint.sh:ro" \
  --entrypoint /bin/sh xcloudsdk-py:0.1.0 /app/capture-entrypoint.sh
#    期望：❌ Xvfb 未能在 10s 内就绪 … 退出码 1

# 3) 生产容器健康检查
docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q)
#    期望：healthy

# 4) 回归：重启容器后必须自愈（这正是历史上会坏掉的操作）
docker restart $(docker compose ps -q) && sleep 25 && docker compose ps
#    期望：Up (healthy)，而不是 Up 但采图 500

# 5) 端到端真抓一张（避开老系统 :01:30 的调用窗口）
curl -s -m 60 "http://127.0.0.1:7003/pool_capture?ip=192.168.1.238&user=admin&storage=local&filename=verify_238" \
  && ls -lt saved_datas/picture | head -3
```

## 五、回滚

```bash
mv docker-compose.override.yml docker-compose.override.yml.off
docker compose up -d --force-recreate          # 回到厂商原入口
# 或直接恢复备份：mv docker-compose.yml.bak.<ts> docker-compose.yml
```

## 六、两个遗留事项（本次未动）

- **宿主 `/etc/systemd/system/xcloud-capture.service`**（`disabled`，指向另一套安装
  `/home/sysadmin/algorithm/image_capture`）与容器**抢同一个 `:99` 和 `:7003`**。
  现在是禁用的所以不会启动，但**别 enable 它**——那会和容器互相顶掉。
- **容器内时钟为 UTC**（宿主 CST，差 8 h）。覆盖层已用 `TZ=Asia/Shanghai` 修掉；
  老系统传的 `filename` 自带宿主时间戳，不受影响。

## 七、2026-09-18 追加：抓拍电平修正（"图像太糊"的第二条根因）

> 完整的调查过程、数字与验收命令在 `../container-cutover.md` §14。这里只写**这个包怎么部署**。

### 问题

厂商 SDK 把满量程输入（相机 RTSP 是 `yuvj420p` + `color_range=pc`）当 limited-range 输出，
写出的 JPEG 服从 `out = 16 + in*219/255`。JPEG 没有范围元数据，浏览器按满量程解码
⇒ 黑停在 16、白停在 235，整幅发灰。实测抓拍 `min/max = 11/239`、近白(>240) **0.00%**；
同一相机 RTSP 直出是 `0/255`。**细节没丢，是电平契约错了。**

### 加了什么

| 新增 | 作用 |
| --- | --- |
| `levels-fix/capture.py` | 补丁版模块，**覆盖**站点包里 `xcloudsdk_py/capture.py` |
| `levels-fix/pylib/` | Pillow 12.2.0（cp310 + manylinux2014，glibc 2.17 ⇒ bullseye 可用） |
| `docker-compose.override.yml` | 追加 `PYTHONPATH` + 两条 `./levels-fix/*` 挂载 |

### 部署顺序（**必须先落文件再 up**）

```bash
B=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cd "$B"
[ -f levels-fix/capture.py ] && [ -f levels-fix/pylib/PIL/__init__.py ] || exit 9   # 源不存在会被建成果目录
cp -a docker-compose.override.yml docker-compose.override.yml.bak.$(date +%Y%m%d%H%M%S)
docker compose config >/dev/null && echo "覆盖层解析 OK"      # ⚠️ 不能带 -f docker-compose.yml
docker compose up -d
docker compose ps
```

动容器**之前**可以先在 `--rm` 一次性容器里验（不碰相机、不影响在跑的服务）：

```bash
docker run --rm -v "$B/levels-fix/pylib:/app/levels-fix/pylib:ro" \
  -e PYTHONPATH=/app/levels-fix/pylib --entrypoint python3 xcloudsdk-py:0.1.0 \
  -c "import PIL; from PIL import features; print(PIL.__version__, features.check('libjpeg_turbo'))"
```

### 验证

```bash
curl -s -m 90 'http://127.0.0.1:7003/pool_capture?ip=192.168.1.238&user=admin&storage=local&filename=check' \
  | grep -o '"levels_[^}]*'
#   期望 "levels_normalized":true, ..., "levels_before":[12,240], "levels_after":[0,255]
python3 ../levels.py <取回的图>      # 端点应贴满 0..255
```

### 回滚

```bash
# 软关（不改文件、不重建）：覆盖层 environment 下加 CAPTURE_NORMALIZE_LEVELS: "0"，再 up -d
cp docker-compose.override.yml.bak.<ts> docker-compose.override.yml && docker compose up -d --force-recreate
```

### 三条硬约束（单测钉住：`third_party/capture` 下 `uv run --no-project --with pytest --with pillow --with boto3 pytest tests -q`）

1. **fail-open**：没 Pillow 或读写失败 ⇒ 原样放行 + WARNING + 响应带 `levels_normalized=false`。
   采图服务掉了就是一张图都没有，比"图发灰"严重得多。
2. **不重复拉**：端点 ≤4 或 ≥251 判为已是满量程 ⇒ 跳过（防厂商修好后被我们再烤一遍）。
3. **不毁原图**：临时文件 + `os.replace` 原子替换。直接 `save` 失败会留下截断的 JPEG，
   而它已经覆盖了唯一一份有效数据。
