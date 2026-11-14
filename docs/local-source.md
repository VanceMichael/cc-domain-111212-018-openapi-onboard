# 本地协议演练

`python3 scenario.py` 启动仅监听回环的业务来源，输出临时端口和可用路由。进程在前台运行，以 Ctrl-C 关闭。

`python3 scenario.py --export` 输出演练响应定义，可按 `docs/source-contract.md` 在独立副本增加乱序、截断、失败或来源更新情形。响应数组顺次消耗并保持最后状态；不会调用真实第三方系统。

请求者可以在测试中用 `from scenario import scenario` 和 `ReplaySource(scenario())` 获得同样来源并检查 requests，避免固定端口冲突。现有 scenario 测试核对来源实际返回值，不表示业务语义采集已实现。

## 接入计划、对比与试采命令

```sh
# 1. 把本地登记文档落到独立目录（不覆盖已有文件）
python3 app.py fixtures /tmp/openapi-onboard-fixtures

# 2. 构建确定性接入计划；授权环境只给“方案名 -> 环境变量名”，计划不含变量值
python3 app.py plan /tmp/openapi-onboard-fixtures supplier-a/1.json \
    --auth-env auth-env.json > plan-a1.json
python3 app.py plan /tmp/openapi-onboard-fixtures supplier-a/2.json \
    --auth-env auth-env.json > plan-a2.json

# 3. 比较两个版本，查看字段、认证与请求编码对已有来源的影响
python3 app.py diff plan-a1.json plan-a2.json

# 4. 仅对批准清单中的只读 operation 试采；凭据值只从进程环境读取
SUPPLIER_A_KEY=local-synthetic-key python3 app.py trial \
    /tmp/openapi-onboard-fixtures supplier-a/1.json approvals.json \
    --auth-env auth-env.json
```

试采目标必须是计划声明且与批准项逐字匹配的回环 server；非 GET/HEAD、计划含阻塞、
server 不匹配、凭据缺失或结构不符都会阻止该 operation，HTTP 200 不能覆盖结构失败。
`supplier-b/1.json` 的所有 operation 均带阻塞项，用于演示“必须人工补充映射”的计划。

标准演练顺序是先准备依赖，再运行 `python3 -m unittest discover -s tests -v`、构建命令和 `python3 app.py demo`。新增能力演示须调用实际新增入口，不以现有结构检查输出代替；演练应选择有限输入，并在九十秒内完成。
