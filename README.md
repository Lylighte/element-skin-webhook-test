# element-skin Webhook 实际应用测试

对 element-skin 的 Webhook 机制进行真实公网部署下的实际应用测试，覆盖完整契约（事件目录、签名、重试、幂等、授权重检、SSRF 防护），并产出性能报告。

## 仓库结构

```
element-skin-webhook-test/
├── README.md                        # 本文件：仓库总览
├── docs/
│   ├── 方案.md                      # 定稿测试方案（本仓库核心文档）
│   └── 部署.md                      # 云服务器部署步骤
├── receiver/                        # FastAPI 接收端（两个 endpoint、SQLite ReplayGuard、面板、故障注入）
│   ├── app.py
│   ├── replay_guard.py
│   ├── verifier.py
│   ├── worker.py
│   ├── playerwall.py                # 应用 A：PlayerWall 皮肤墙业务
│   ├── probe.py                     # 应用 B：Webhook Probe 边界路径
│   ├── config.py
│   ├── requirements.txt
│   └── tests/
├── deploy/
│   ├── nginx.conf                   # skin.* 与 hooks.* 双域名
│   ├── receiver.service             # systemd 单元
│   └── setup.sh                     # 一键部署脚本
├── scripts/                         # 测试驱动（API 序列）
│   ├── create_apps.py
│   ├── authorize_a.py
│   ├── drive_events.py
│   ├── fault_tests.py
│   └── perf.py
└── report/
    └── webhook-real-deployment-test.md   # 最终测试报告（含性能）
```

## 两个应用

| 应用 | 类型 | 权限 | 订阅事件 | 定位 |
| --- | --- | --- | --- | --- |
| A · PlayerWall | 公开（用户委托） | `profile.read.owned`、`texture.read.owned`、`oauth_grant.read.owned` | `profile.*`、`texture.*`、`oauth_grant.revoked` | 社区官网玩家皮肤墙，撤销授权即下架 |
| B · Webhook Probe | 机密（app-only） | `permission.read.any`、`account.read.any`、`official_whitelist.read.any`、`oauth_grant.read.owned` | `permission.updated`、`account.*`、`official_whitelist.*`、`oauth_grant.*` | 覆盖管理型事件与定向投递边界 |

覆盖全部 15 种事件类型。

## 快速开始

1. 阅读 [docs/方案.md](docs/方案.md) 了解完整测试方案；
2. 按 [docs/部署.md](docs/部署.md) 在云服务器部署；
3. 运行 `scripts/` 下的测试驱动脚本；
4. 查看 `report/` 下的测试报告。

## 状态

- [x] 方案定稿（docs/方案.md）
- [x] 接收端代码（receiver/，14 个 pytest 全通过 + 端到端冒烟通过）
- [x] 测试驱动脚本（scripts/，含 site_client 共享客户端）
- [x] 部署文件（deploy/ + docs/手动部署清单.md）
- [ ] 云服务器部署（用户手动部署中）
- [ ] 功能测试执行
- [ ] 性能测试与报告
