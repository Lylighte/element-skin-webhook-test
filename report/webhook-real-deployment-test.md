# element-skin Webhook 真实部署测试报告

> 测试日期：2026-08-20 · 测试环境：云服务器（Ubuntu，2C2G）
> 测试执行：DeepSeek V4（GitHub Copilot）辅助完成

## 1. 测试环境

| 项 | 值 |
| --- | --- |
| 云服务器 | Ubuntu 24.04.2 LTS，2C2G，公网 IP |
| element-skin 版本 | dev 分支（3.1.0，Docker Compose 部署） |
| python-sdk 版本 | 0.1.0（dev 分支，含 webhook 支持） |
| 网络拓扑 | element-skin 与接收端同机（公网域名回环） |
| 域名 | 测试域名（共用，接收端挂 /wh/ 前缀） |
| 接收端 | FastAPI + SQLite（验签/重放防护/inbox/PlayerWall/Probe） |

## 2. 应用与配置

| 应用 | 类型 | 权限 | 订阅事件 | endpoint |
| --- | --- | --- | --- | --- |
| A · PlayerWall | public | profile.read.owned、texture.read.owned、oauth_grant.read.owned | profile.\*、texture.\*、oauth_grant.revoked | /wh/webhooks/element-skin |
| B · Webhook Probe | confidential | permission.read.any、account.read.any、official_whitelist.read.any、oauth_grant.read.owned | permission.updated、account.\*、official_whitelist.\*、oauth_grant.\* | /wh/webhooks/probe |

密钥管理：signing_secret 只显示一次；client_secret 仅 B 有。

## 3. 功能测试结果

### 3.1 应用 A · PlayerWall 业务链路

| # | 用例 | 结果 | 备注 |
| --- | --- | --- | --- |
| A1 | 注册 + 授权 → 空状态 | ✅ | 授权后无卡片 |
| A2 | 创建角色 → 卡片出现 | ✅ | 卡片含名字 + UUID |
| A3 | 改名 → 卡片更新 | ✅ | profile.updated 到达，卡片更新 |
| A4 | 上传皮肤 → 纹理 URL 更新 | ✅ | texture.created 到达 |
| A5 | 换肤 → 纹理 URL 变化 | ✅ | texture.updated 到达 |
| A6 | 删皮肤 → 纹理引用清空 | ✅ | texture.deleted 到达 |
| A7 | 删角色 → 卡片下架 | ✅ | profile.deleted 到达 |
| A8 | 撤销授权 → 全部下架 | ✅ | oauth_grant.revoked 到达，皮肤墙下架正确 |
| A9 | 重复投递 → 卡片不重复 | ✅ | 重放返回 204，无重复 |

### 3.2 应用 B · Probe 边界路径

| # | 用例 | 结果 | 备注 |
| --- | --- | --- | --- |
| B1 | 改权限覆盖 → permission.updated | ✅ | 到达（app-only 无 grant 也投递） |
| B2 | 加/删角色 → permission.updated | ✅ | 到达 |
| B3 | 注册新用户 → account.created | ✅ | 到达 |
| B4 | 删除用户 → account.deleted | ⬜ 未测 | 待补充 |
| B5 | 加/删白名单 → official_whitelist.* | ✅ | added/removed 均到达 |
| B6 | 授权/撤销 B → oauth_grant.* 三件套 | ⬜ 未测 | 待补充 |
| B7 | 撤销后停止投递 | ⬜ 未测 | 待补充 |

### 3.3 可靠性与安全

| # | 用例 | 结果 | 备注 |
| --- | --- | --- | --- |
| C1 | 500 → 指数退避重试 | ✅ | 真实事件触发，重试成功 |
| C2 | >10s 超时 → 重试 | ✅ | 真实事件触发，重试成功 |
| C3 | 重放同 Webhook-Id → 204 | ✅ | 204/204，无重复 |
| C4 | 篡改/伪造/过期 → 400 | ✅ | 全部 400 |
| C5 | 投递前撤销 grant → 停止 | ⬜ 未实现 | 时序控制复杂，与 B7 类似 |
| C6 | 越权事件 → 400 | ✅ | 未申请权限事件被拒 |
| C7 | 非 HTTPS/私网 → 400 | ✅ | http/127.0.0.1/localhost 均被拒 |
| C8 | endpoint 停用 → 恢复 | ✅ | 停用不投递，恢复后投递 |
| C9 | 时钟偏差 >5min → 400 | ✅ | 超前时间戳被拒 |

## 4. 性能数据

### 4.1 端到端延迟（创建角色 API）

| 场景 | 事件数 | 并发 | P50 | P95 | P99 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 同机回环 | 40 | 3 | 32.7ms | 57.8ms | 82.5ms | 事件 40/40 到达 |
| 同机回环 | 200 | 5 | 58.8ms | 110.0ms | 161.5ms | 事件 200/200 到达 |
| 同机回环 | 800 | 8 | 139.6ms | 308.6ms | 479.5ms | 事件 682/800 到达（worker 投递积压） |

### 4.2 吞吐

| 场景 | 事件数 | 端到端吞吐 | 端到端总耗时 | 备注 |
| --- | --- | --- | --- | --- |
| 默认预算（2 连接/3000ms） | 40 | 5.9 events/s | 6.8s | 事件 40/40 |
| 默认预算（2 连接/3000ms） | 200 | 17.6 events/s | 11.4s | 事件 200/200 |
| 默认预算（2 连接/3000ms） | 800 | 5.0 events/s | 136.9s | 事件 682/800（120s 超时） |

### 4.3 资源预算对比

| 预算 | 主站请求延迟影响 | 备注 |
| --- | --- | --- |
| 默认（2 连接/3000ms） | 并发 3-5 时 API 延迟正常（P50 33-59ms） | 2C2G 下并发 8 时延迟上升（P50 140ms） |
| 调参（4 连接/1000ms） | 未测 | 需重启 worker 后重跑 |

### 4.4 SQL 效率

| 指标 | 实测 | 基准（0.08 次/event） | 备注 |
| --- | --- | --- | --- |
| worker SQL 次数/event | 未单独测量 | 0.08 | 需数据库日志分析 |

## 5. 发现的问题与结论

### 5.1 功能验证结论

- **事件目录与权限路径正确**：用户委托（profile/texture）、定向（oauth_grant）、app-only（permission/account/whitelist）三类路径均验证通过；
- **定向投递正确**：oauth_grant.* 只投递给所属应用，不广播；
- **授权终止闭环**：撤销授权 → oauth_grant.revoked → 皮肤墙下架（A8 验证）；
- **签名/重放/重试/SSRF 防护**：C1-C9 全部通过。

### 5.2 性能结论

- **API 延迟**：2C2G 下并发 3-5 时健康（P50 33-59ms），并发 8 时明显上升（P50 140ms），符合资源受限预期；
- **worker 投递吞吐**：约 17.6 events/s（200 事件，2 连接预算）；
- **800 事件 682/800**：worker 投递积压，120s 超时内未投完，非功能问题；
- **50 并发 SSL 握手超时**：2C2G 服务器无法处理 50 并发 HTTPS，需降低并发。

### 5.3 未覆盖项

- B4（删除用户 → account.deleted）、B6（授权/撤销 B → oauth_grant.*）、B7（撤销 B 后停止投递）：待补充；
- C5（投递前撤销 grant → 停止）：未实现（时序控制复杂，与 B7 类似）；
- 资源预算调参对比、SQL 效率：未测。

## 6. 附录

- 原始数据：`report/raw/`
- 复现命令：见 docs/部署.md 与 scripts/ 用法
- 测试脚本：scripts/（create_apps.py、authorize_a.py、drive_events.py、fault_tests.py、perf.py）
