# 来源格式与业务语义

## OpenAPI 接入计划（确定性，不猜测）

`registry.py` 只从本地登记目录加载 JSON 文档并解析引用闭包：文档内 `#/...` 指针、
目录内相对路径（含 `../` 但不得越界）允许；外部 URL、scheme、绝对路径与未登记文档
一律拒绝，不发生任何网络下载。递归 `$ref` 合法但会在形状上标记 `recursive` 并阻塞试采。

`planner.py` 把登记文档构建为确定性接入计划（同文档重复构建字节级一致）：

- 合并 path 级与 operation 级 `parameters`，同名同位置时 operation 级覆盖 path 级；
- 参数序列化只支持契约声明的位置/样式子集：query 的 `form`/`spaceDelimited`/
  `pipeDelimited`/`deepObject`、path/header 的 `simple`、cookie 的 `form`；
  `label`、`matrix`、参数 `content`、样式与数据类型不匹配等均产生阻塞；
- schema 支持 `type`（含 3.1 的类型数组）、3.0 的 `nullable`、`properties`、
  `required`、`items`、`enum`、`oneOf`/`anyOf` 与本地 `$ref`；`allOf`、
  `additionalProperties` 等超出契约的关键字明确诊断，不假称完整 JSON Schema；
- 请求侧的多分支联合（无法确定编码）、多 media type、非 JSON 请求体、递归/不可解析
  形状均为阻塞项；`oneOf/anyOf` 若仅为“单类型 + null”，归一化为 nullable 形状；
- 每个 operation 自带局部 `blockers`，只读（GET/HEAD）且无阻塞才可试采；
  计划顶层汇总全部阻塞，供人工补充映射。

## 凭据与安全方案

operation 显式 `security` 覆盖文档根级；每个备选方案的成员按 `components/securitySchemes`
解析。试采仅支持 apiKey（header/cookie；query 注入不开放）与 http bearer，
oauth2/openIdConnect 等标记为 `UNSUPPORTED_SECURITY`。

凭据只通过授权环境绑定（方案名 -> 环境变量名），计划与任何导出只记录环境变量名，
绝不包含变量值；试采时变量值只从进程环境读取，缺失或为空即阻塞且不发请求。

## 批准清单与自动试采（`trial.py`）

试采只执行批准清单显式列出的 operation，并逐条强制：

1. operation 在计划内、方法为 GET/HEAD、且计划无阻塞——POST 等即使带示例也不调用；
2. 批准 server 与计划声明逐字匹配，不自动在多个 server 间选择；
3. server 必须是回环 `http://127.0.0.1`/`localhost`，不做 DNS 解析、不访问外网；
4. 输入只能使用计划声明的参数，必填参数缺失或出现未声明参数即阻塞；
5. 只读试采不发送请求体。

## 响应验证（`validator.py`）

响应按**实际**状态码与 content type 选择对应 schema：精确状态优先，`default` 兜底；
media type 精确或 `type/*` 匹配。HTTP 200 但 JSON 解析失败、media type 未声明、
或结构不符（缺必填字段、类型不符、enum 越界、oneOf 匹配数不等于一等）一律判定失败，
`onboarding_complete` 不得为真。无响应体声明的状态不做结构验证并明确标注。

## 版本对比（`plandiff.py`）

以 `(method, path)` 锁定 operation，比较参数增删、`style`/`explode`/必填性、请求体
media type 与形状、security 备选（含 env 名）、server 增删、响应 schema 字段增删改、
阻塞项与试采资格变化。影响已有来源的变化（删字段/收紧类型/改编码/改认证/移除 server/
新增必填等）标记 `breaking=true` 并汇总 `affects_existing_source`。

## 本地夹具与演练

本地演练夹具由 `sampledata.py` 生成，均为虚构业务数据：`supplier-a/1.json` 与
`supplier-a/2.json` 是同一供应商的两个版本（含认证、请求编码与响应字段变化），
`supplier-b/1.json` 是必须人工补充映射的阻塞计划（外链、未知样式、allOf、xml、
oauth2、递归外关键字等），`shared/external.json` 用于验证目录内跨文件引用闭包。
生成不会下载资源。

本地 HTTP 演练器采用方法与原始路径作为路由键，值为按调用次数返回的响应数组，
耗尽后维持最后一项。响应可定义 status、headers、text/base64、require_headers、
chunk_bytes、disconnect_after。每次请求保留方法、路径、头与正文摘要表示，
便于核对实际访问；演练器只监听回环地址。

原始输入上限 16 MiB 是现有查看入口的保护值，不代表未来持续采集需一次读完整来源。
格式含义和截断错误不能被 HTTP 200 覆盖。
