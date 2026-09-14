# 部署指南（Ubuntu + systemd）

> 如果你不想使用 systemd，可直接用 Docker 运行：`docs/DOCKER.md`

## 1. 准备目录
示例使用：
- 项目目录：`/home/sysadmin/algorithm/xcloudsdk_py`
- 服务端口：`7003`

把以下文件/目录放到项目目录下：
- `src/`、`pyproject.toml`、`README.md`
- `lib/x86_64/Release/libXCloudSDK.so`
- `XCloudSDKTest_config.ini`（生产环境真实配置；JSON 文本）
- `minio_config.json`（可选；`storage=cloud` 需要；生产环境真实配置）

## 2. 安装 Python 依赖
```bash
cd /home/sysadmin/algorithm/xcloudsdk_py
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## 3. 安装 systemd 服务
### 3.1 一键安装/升级（推荐）
在项目目录执行：
```bash
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

默认会安装为 `xcloud-capture-py` 服务，端口 `7003`；可用环境变量覆盖：
```bash
SERVICE_NAME=xcloud-capture PORT=7003 PROD_DIR=/home/sysadmin/algorithm/image_capture_tt ./scripts/install_service.sh
```

### 3.2 手动安装（可选）
1) 把 `systemd/xcloud-capture-py.service` 拷贝到 `/etc/systemd/system/` 并按你的路径修改：
```bash
sudo cp systemd/xcloud-capture-py.service /etc/systemd/system/xcloud-capture-py.service
sudo systemctl daemon-reload
sudo systemctl enable xcloud-capture-py
sudo systemctl start xcloud-capture-py
```

2) 查看状态与日志：
```bash
sudo systemctl status xcloud-capture-py --no-pager
sudo journalctl -u xcloud-capture-py -f
```

## 4. 定时触发（可选）
### 4.1 使用 crontab
```bash
crontab -e

# 每小时第 5 分钟触发一次（建议用 pool_capture + filename 带时间戳）
5 * * * * /usr/bin/curl -s "http://localhost:7003/pool_capture?ip=192.168.1.231&user=admin&storage=cloud&filename=hourly_$(date +\\%Y\\%m\\%d_\\%H)" >> /var/log/xcloud-hourly.log 2>&1
```

### 4.2 使用 systemd timer（更推荐）
你也可以按公司规范创建 `xcloud-capture.timer` + `xcloud-capture@.service` 来触发 curl。
