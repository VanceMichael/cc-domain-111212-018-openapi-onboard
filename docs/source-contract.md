# 来源格式与业务语义

文档只从本地登记资源解析引用，不任意下载 $ref。计划支持已声明的 path/query/header/cookie 参数及 simple/form/spaceDelimited/pipeDelimited/deepObject 子集，未知 style 阻塞。批准清单以 server+operationId 锁定，不因 HTTP 方法自动获得授权；仅批准只读探测。凭据从运行配置注入，plan/export 不含真实值。schema 验证支持 type、properties、required、items、enum、oneOf/anyOf、nullable 和本地 $ref；超出范围明确诊断，不假称完整 JSON Schema。

本地演练夹具由 `sampledata.py` 生成，均为虚构业务数据。`sample` 是当前可查看输入，`invalid` 是边界失败输入，其余用于对照来源变体。生成不会下载资源。

本地 HTTP 演练器采用方法与原始路径作为路由键，值为按调用次数返回的响应数组，耗尽后维持最后一项。响应可定义 status、headers、text/base64、require_headers、chunk_bytes、disconnect_after。每次请求保留方法、路径、头与正文摘要表示，便于核对实际访问；演练器只监听回环地址。

原始输入上限 16 MiB 是现有查看入口的保护值，不代表未来持续采集需一次读完整来源。格式含义和截断错误不能被 HTTP 200 覆盖。
