# openapi-onboard

新数据接口的受控接入规划与试采所用的来源检查、确定性接入计划与原件留存工具。

## 当前能力

- `source.py` 查看来源的现有结构，不把检查结果冒充完整业务采集。
- `storage.py` 将原始字节、来源标识、摘要和检查结果一起保存在本地 SQLite，重复原件保留同一收件编号。
- `sampledata.py` 构造可重复的本地数据；`transport.py` 提供独立的回环 HTTP 演练源与请求记录。
- `registry.py` 只从本地登记目录解析 OpenAPI 文档与允许的 `$ref` 引用闭包，拒绝外链与越界引用。
- `planner.py` 输出确定性接入计划：合并 path/operation 参数，处理 simple/form/space/pipe/deepObject
  序列化样式、nullable 与联合 schema；无法确定请求形状时给出阻塞（blocker）而非猜测。
- `validator.py` 按实际状态码与 content type 对应的 schema 验证响应，HTTP 200 但结构不符不算接入完成。
- `trial.py` 仅对批准清单中无阻塞的 GET/HEAD operation、且逐字匹配的回环 server 自动试采；
  凭据只按显式 security 备选与授权环境绑定，计划/报告/导出均不含真实值。
- `plandiff.py` 比较两个版本的计划，标注字段、认证、请求编码、server 等影响已有来源的变化。

解释范围和来源变体见 `docs/source-contract.md`，演练命令见 `docs/local-source.md`；
当前检查测试与将来新增业务能力的测试分开保留。

## 环境

Python 3.11 或以上，仅标准库，无需凭据和外部数据。使用容器时可执行 `docker build -t openapi-onboard .`，依赖及镜像须在正式运行前准备。
运行入口、测试与演示均不安装包，不访问公网。数据库使用临时路径，不向代码目录写入业务文件。

## 测试与构建

构建工具版本固定在 `requirements-build.txt`，在环境准备阶段执行 `python3 -m pip install -r requirements-build.txt`；容器构建已包含此步骤。应用运行仍仅使用标准库，测试及演示不安装依赖。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py source.py sampledata.py storage.py transport.py scenario.py registry.py planner.py validator.py trial.py plandiff.py
python3 -m build --wheel --no-isolation
```

## 快速演示

```sh
python3 app.py demo
```

演示在临时目录登记两个供应商的多版本文档，真实启动本地回环来源，并展示：

1. 供应商 A 的可试采计划：一条通过 schema 验证，一条 HTTP 200 但缺必填字段而失败，
   整体 `onboarding_complete=false`；写操作（POST）即使在批准清单中也不会被调用；
2. 供应商 B 的阻塞计划：外部引用、未知样式、allOf、非 JSON media type、不支持的
   security 等逐项诊断，所有 operation 均不可试采，须人工补充映射；
3. A 的 v1/v2 计划对比：认证备选、query 编码、必填性、字段类型与新增必填字段的
   破坏性变化汇总；
4. 既有的来源检查、真实 HTTP 取件与 SQLite 收件身份演示。

正常在数秒内完成，不以预录日志代替执行，退出后自动清理。

## 单独检查、计划与收件

```sh
python3 app.py fixtures /tmp/openapi-onboard-fixtures
python3 app.py inspect /tmp/openapi-onboard-fixtures/sample.json
python3 app.py plan /tmp/openapi-onboard-fixtures supplier-a/1.json --auth-env auth-env.json
python3 app.py capture /tmp/openapi-onboard-fixtures/sample.json --database /tmp/openapi-onboard.sqlite3 --source local-partner
python3 app.py list --database /tmp/openapi-onboard.sqlite3
```

生成夹具不会覆盖已有文件。运行错误以 JSON 输出到标准错误并返回退出码 2；原始输入无效时不生成成功收件。内部原件与任何对外展示副本应分开管理，不写入真实个人数据。
