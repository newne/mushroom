# 07 — 凭据文件入库：`src/configs/.secrets.toml` 被 git 跟踪（只出票、不动手）

**Spec:** 仓库安全（无对应 spec 章节）
**归属:** 处置执行 = 发版官（涉及生产凭据轮换，**必须用户裁**）；出票 = 测量师。
**Status:** ready-for-human（**不要先动手**：`git rm --cached` 本身安全，但轮换要用户批准）

## 现象（2026-10-14 核实）

- `git ls-files src/configs` ⇒ **`src/configs/.secrets.toml` 在版本控制里**，
  即**凭据进入了仓库与全部历史**；
- `.gitignore:114` 那条对**已跟踪文件无效**（gitignore 只影响未跟踪文件），
  所以"加 .gitignore"并不能修；
- 全仓扫描凭据类文件名：`git ls-files | grep -iE 'secrets|\.env|credential|\.pem$|\.key$|token'`
  ⇒ **只命中这一个**（本次排查**全程未打印任何凭据值**，只报路径与段名/键名）。

## 处置建议（两步，顺序不能反）

1. **停止继续跟踪**：`git rm --cached src/configs/.secrets.toml`
   —— **本地文件保留**（工作区不受影响），`.gitignore` 里确认已忽略该路径（`114` 行附近已有）；
   之后 `git status` 应显示它被删除跟踪、但磁盘上仍在。
   ⚠️ 这**只是止血**：历史里的值删不掉，任何拿到仓库的人仍能读到。
2. **轮换（真修）**：把该文件里的生产凭据**全部换一遍**（pgsql / redis / minio / llama / prompt
   等段），因为旧值已暴露在历史中。轮换涉及**生产数据库、对象存储与推理服务**，
   必须用户批准后由发版官执行，并按"改一处验一处"推进（轮换期间旧值需短暂共存）。

## 不做什么

- **不**用 `filter-repo`/`filter-branch` 重写历史：共享分支禁止重写（`docs/agents/bot-roles.md`），
  而且重写也追不回已经克隆出去的副本 ⇒ 只有**轮换**是真修；
- **不**在聊天/票据里贴任何凭据值（本票只有路径与段名）；
- **不**改 `.secrets.toml` 的内容（除了将来轮换那一次），不删 mysql 段
  （那是另一张票：`fetch_room.py` 迁 pgsql 完成后再清）。

## 验收

- [ ] `git ls-files | grep secrets` 为空（不再跟踪）
- [ ] `.secrets.toml` 仍在磁盘上，服务读得到（本地/容器路径不变）
- [ ] 生产凭据完成轮换，且**旧值已失效**（逐项验证：pgsql 连通、redis、minio、llama 调用）
- [ ] 记录轮换日期与影响面（哪几个服务重启过）

## Comments

- 2026-10-14 写票（测量师）。起因：清理 `mysql_engine` 时核对 `settings.toml`/`.secrets.toml`，
  发现后者**被 git 跟踪**；Chief 核过 `git ls-files` 确认，并要求"单独开票、不动手"。
  同批复核结论：`.secrets.toml` 的 `[development.mysql]` / `[production.mysql]` 两段
  **保留**（仓外消费者未证实；等 `fetch_room.py` 迁完 pgsql 再一次性清）。
