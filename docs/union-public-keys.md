# 联合公钥 / Union public keys (1.4.0)

## 目标与边界

各站独立持有私钥、独立选择合作站，不需要中心签发服务，也不分发私钥。客户端从自己选定的认证站获取批准的公钥集合，逐把验签；未知签名、篡改内容和缺失签名不会被接受。

这属于**显式多根互信**，不是“无需信任”。被加入某一用途的公钥获得该用途的全局签名信任；本协议不按 UUID、账号归属或材质域名限制单把密钥的签发范围。合作站若被入侵，攻击者可能利用其密钥伪造其他站的材质/证书。管理员应通过可信渠道核对公钥指纹，并分别审查两种用途。不要自动抓取任意玩家 profile 中的密钥或信任地址。

本改动只处理验签信任，不实现联合登录、账号映射、UUID 冲突处理或认证后端。服务端 HTTP 200 只能证明某次请求成功，不足以单独证明客户端验签、缓存、域名白名单、材质解码和渲染全部正常；仍需真实客户端联调。

## 认证站接口

在现有 Yggdrasil API 根元数据的 `meta` 对象中增加布尔标志：

```json
{
  "meta": {
    "feature.enable_public_key_fetching": true
  },
  "skinDomains": ["textures.site-a.example", "textures.site-b.example"],
  "signaturePublickey": "-----BEGIN PUBLIC KEY-----\n...本站旧格式公钥...\n-----END PUBLIC KEY-----"
}
```

`signaturePublickey` 可以保留给旧客户端，但开启上述标志后，**新接口是自定义密钥的唯一来源**。不会自动将旧字段添加到两个用途，也不会在接口故障时退回旧字段。旧客户端仍只有旧能力，不能靠服务器设置让它自动支持多公钥。

agent 启动时仅检查配置，不请求公钥接口。首次进入支持的验签入口或读取本地 `/publickeys` 时，在后台请求：

```http
GET {API根}/minecraftservices/publickeys
Accept: application/json
```

例如 API 根是 `https://auth.site-a.example/api/yggdrasil/`，则路径是 `/api/yggdrasil/minecraftservices/publickeys`，不是网站根的 `/publickeys`。启动器使用预取元数据参数时，同样延迟到首次使用公钥才请求；这不改变原有元数据加载流程。此公钥请求不添加玩家 accessToken、Authorization 或游戏会话数据，接口应当公开可读。

返回 HTTP 200 与以下结构：

```json
{
  "profilePropertyKeys": [
    {"publicKey": "<本站材质签名公钥的 Base64 X.509 DER>"},
    {"publicKey": "<经批准的合作站材质签名公钥的 Base64 X.509 DER>"}
  ],
  "playerCertificateKeys": [
    {"publicKey": "<本站玩家证书签名公钥的 Base64 X.509 DER>"}
  ]
}
```

要求：

- 两个数组都必须存在；可以显式为空。重复公钥会去重。
- `publicKey` 是无 PEM 头尾的标准 Base64 编码，内容为 X.509 SubjectPublicKeyInfo DER，不是 PKCS#1 裸 RSA key，也不是证书文件。
- 每个用途最多 64 项，RSA 密钥长度为 2048–8192 位，整个响应最多 128 KiB。
- 默认保留 agent 自带的 Mojang 公钥（两个用途）；空数组仅表示不信任该用途的自定义密钥。此功能不更新 Mojang 自身的密钥轮换机制。
- 生产环境必须使用 HTTPS。HTTP 只允许 `localhost`、`127.0.0.1`、`[::1]`，用于受信任的本地桥接。公钥接口不跟随重定向；连接和单次读取超时均为 10 秒。这不是整个下载的总时限，调用方等待上限另见下节。
- 接口不可用、非 200、非法 JSON/密钥或过大的响应均视为刷新失败，不接纳该响应；不会因公钥接口故障直接终止 JVM 启动。尚在最大年龄以内的旧集合可继续使用。
- **降级基线（v1.4.1）**：首次加载失败或缓存过期后，信任集回退为【元数据 `signaturePublickey` ∪ 内置 Mojang】——本站自身签名的材质在降级期仍可验证，不再退化为仅 Mojang 导致全站材质失效；仍不静默放行任意未知密钥。
- 元数据标志类型、API 根或刷新参数非法仍会使正常 `-javaagent` 启动失败。
- 不支持与 `-Dauthlibinjector.disableHttpd` 同时开启；本地 HTTP 服务用于新 authlib 公钥入口的兼容。

没有启用标志（缺失或 `false`）时不访问新接口、不启动刷新定时器，继续把旧 `signaturePublickey` 用于材质和证书验签；内置 Mojang 公钥仍保留。元数据标志和 API 根本身仍是启动配置，本版本不热切换认证站。

## 按需加载与运行中刷新

“按需”指延迟加载**本站已批准的完整公钥集合**，不是跟随任意玩家提供的 URL 下载密钥，也不是遇到谁就把全局公钥切成谁的。接口仍只来自玩家启动时选定的认证站，不引入中央签发服务。

1. 启动时没有公钥网络请求和刷新线程；首次需要密钥的验签调用或本地公钥接口访问激活后台工作线程。明显缺失、空或过大的签名直接拒绝。
2. 验签先使用当前未过期集合。首次加载、缓存过期或验签失配时，可合并到同一个后台请求；同一时刻最多一个请求，所有触发来源共用冷却时间。
3. 失配检查最多等待 `waitMillis`，再用当时的集合重验一次。最终仍不能用当前未过期集合验签就拒绝，下载失败或等待超时都不能作为放行依据；等待超时不取消其他调用共用的下载。
4. 首次激活后即使没有失配也定期刷新，以发现撤销。新集合完整替换旧集合，不累积已撤销公钥；失败不刷新旧集合的年龄。验签期间若集合发生变化或到期，也会检查当前状态，不直接沿用旧集合的成功结果。
5. 两种用途始终独立。签名对象在真正 `verify()` 时读取当前证书信任；Velocity 的 `isSignatureValid()` 入口也重新验签，不复用旧的布尔结果。本地 `/publickeys` 返回当前有效集合并携带 `Cache-Control: no-store`。

JVM 参数（仅新接口模式生效）：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `authlibinjector.publicKeys.refreshSeconds` | 300 | 成功获取后，经过多久应再次刷新 |
| `authlibinjector.publicKeys.maxAgeSeconds` | 900 | 从最近一次成功获取算起，自定义密钥的最大可用年龄 |
| `authlibinjector.publicKeys.cooldownSeconds` | 30 | 两次请求开始时间的最小间隔，也用于周期检查 |
| `authlibinjector.publicKeys.waitMillis` | 2000 | 单次调用等待正在进行的下载的最长毫秒数 |

前三项须为整数秒，满足 `0 < cooldown <= refresh <= maxAge <= 86400`；等待范围为 `0..10000` 毫秒。配置示例：

```text
-Dauthlibinjector.publicKeys.refreshSeconds=300
-Dauthlibinjector.publicKeys.maxAgeSeconds=900
-Dauthlibinjector.publicKeys.cooldownSeconds=30
-Dauthlibinjector.publicKeys.waitMillis=2000
```

定期任务按冷却间隔检查是否到期；实际刷新完成时间还受线程调度、已有请求和网络耗时影响，不能把 300 秒当作撤销生效的严格时限。缓存采用单调时钟且不落盘；进程重启不会复用上次启动的响应。超过最大年龄即排除自定义公钥，即使后台请求仍未结束。

**同步兼容边界：**旧 authlib 验签方法必须同步返回通过/失败。HTTP 在后台线程执行，但首次加载或失配的调用方默认仍可能等待最多 2 秒（不含本地验签耗时）；不是“渲染线程绝不阻塞”。`waitMillis=0` 可完全不等网络，但密钥未就绪时会先拒绝，之后需要游戏再次调用验签。本版本不对所有旧 MC 版本实现皮肤失败缓存清理、自动重试或重新绘制。已有可用集合能通过的检查直接返回，定期下载不要求它等待。

## 两站部署示例

若 A 服会将所有玩家的材质重签为 A 的签名，B 玩家要显示这些材质，**B 站返回给 B 客户端的 `profilePropertyKeys` 必须包含 A 的公钥**。反向同理。也可保留原始签发站签名，只要接收方信任相应公钥。

1. A、B 管理员各自核对并配置本站和对方的材质公钥，不交换私钥。
2. 两站独立实现新接口，然后在元数据中启用功能标志。不要在接口未就绪时先开标志。
3. 两边玩家实际加载此分支的 agent JAR；需要验证对应签名的服务端/代理也加载该 JAR。无需修改启动器源码，但必须确认其支持选用/替换 agent，并且不会在启动时下载原版覆盖它。只升级服务器 JAR 不会改变玩家客户端的信任根。
4. 两边 `skinDomains` 仍须包含实际材质域名。多公钥不会自动加入任意域名；不要以 `*` 代替审核。
5. 更换 JAR 后重启客户端/服务器，确认日志包含 `Version: 1.4.0...` 与 `Lazy public-key fetching enabled`。首次使用后应出现 `Refreshed trusted public keys` 和两类数量，再用全新会话验证双方材质、旧缓存刷新和聊天。仅看到启动成功不证明公钥加载成功。
6. 在测试环境增添、轮换和移除公钥，观察同一进程中后续验签是否变化；同时验证接口故障、缓存到期和游戏自身缓存行为。

现有启动参数格式不变：

```text
-javaagent:/absolute/path/authlib-injector-union-1.4.0.dirty.jar=https://auth.site-a.example/api/yggdrasil/
```

## 聊天证书与兼容性变更

材质互信不会自动信任合作站的聊天/玩家证书。只有需要并已审核证书签发能力时，才把对应公钥加入 `playerCertificateKeys`。这两类公钥可以使用不同密钥对，也可以按现有后端情况显式使用同一把。

本分支去除了继承的无条件证书验签成功行为。`feature.enable_profile_key` 未启用（或被 JVM 配置禁用）时，本地 `/player/certificates` 返回 HTTP 204，不再生成带 `AA==` 假签名的证书。

需要安全聊天的后端必须启用现有 `feature.enable_profile_key=true`，并在 `POST {API根}/minecraftservices/player/certificates` 提供真正签名的玩家证书。该证书请求是已有的认证流程，与上文公开公钥请求不同；应由后端验证会话身份。UUID 绑定、过期时间等必须正确；agent 的证书验签使用现有 SHA1withRSA 协议格式，生命周期/过期检查仍由游戏或代理调用者负责。为能用新集合重验，证书签名适配器最多缓冲 64 KiB 待验数据，超限拒绝，不接受截断内容。

依赖旧假证书或始终成功验签的部署将不再兼容。若游戏/服务器要求安全档案而后端不提供真证书，会失败；不要靠关闭验签恢复。迁移时需让客户端重新获取合法证书、测试缓存和具体 MC/代理版本，不能只替换公钥。

代理证书格式核对来源：[Velocity IdentifiedKeyImpl](https://github.com/PaperMC/Velocity/blob/dev/3.0.0/proxy/src/main/java/com/velocitypowered/proxy/crypto/IdentifiedKeyImpl.java)、[BungeeCord EncryptionUtil](https://github.com/SpigotMC/BungeeCord/blob/master/proxy/src/main/java/net/md_5/bungee/EncryptionUtil.java)。这些链接指向上游开发分支，后续版本仍需兼容性验证。

## 轮换与撤销

1.4.0 新接口模式中，公钥响应是**可替换的运行中快照**：无需为每次更新重启进程；轮询或失配触发的成功刷新后，后续验签使用新集合。旧元数据固定模式不参与热刷新；1.3.0 客户端仍只有启动时快照，更早的客户端也不会自动获得多公钥能力。

计划轮换时，先同时公布新旧公钥，留出各客户端获取新钥的时间，再启用新私钥签名；等旧签名数据/证书完成迁移后移除旧钥。若在冷却期内才加入新钥，失配不能绕过冷却，可能暂时看不到材质。因此应先发布公钥，再切换签发。

泄露时，在**每个批准过该公钥的认证站**撤销相应用途；在线 1.4.0 进程要等成功刷新后才获知撤销。接口失联时，旧信任最多用到最近一次成功获取后的 `maxAgeSeconds`，默认 15 分钟；这不是即时远程吊销。紧急场景仍需协调重新连接、清理游戏缓存或重启，不应等待正常轮询代替应急处置。

本版本不自动作废已经建立的会话、不移除已经渲染的皮肤，也无法控制未再次调用受支持验签入口的外部缓存。它不会自动迁移旧证书或动态更新 `skinDomains`。私钥轮换、证书重新签发、材质域名与旧缓存应共同规划。

## 构建与验证范围

JDK 17（或项目支持的构建 JDK）与 Gradle 8.0.2：

```sh
./gradlew test shadowJar
```

产物位于 `build/libs`，目标字节码为 Java 8，未正式打标签的开发构建保留 `.dirty` 后缀。

自动化覆盖公钥解析、双站验签、用途隔离、错误/篡改签名、传输限制、预取元数据，以及按需加载、并发请求合并、冷却、有限等待、后台轮换、硬过期、验签途中撤销和既有证书对象更新。独立 JVM 中的打包 agent 测试覆盖首次使用、故障启动、运行中撤销和失配重试。打包测试的 authlib/Velocity/BungeeCord 类是本仓自有最小测试夹具，不是完整 Minecraft 或代理发行版；通过这些测试不等于已经完成两站真实游戏环境验收。

## English summary

Opt in with the Boolean metadata flag `meta["feature.enable_public_key_fetching"] = true`. In 1.4.0 the first supported verification or local `/publickeys` read activates fetching from `{selected API root}/minecraftservices/publickeys`, including with launcher-prefetched metadata. Each key is a Base64-encoded RSA X.509 SubjectPublicKeyInfo. Texture and player-certificate trust remain separate, and the bundled Mojang key is retained. Sites independently approve peers; no central signer or shared private key is required. Trust is global per purpose, not scoped to a peer's account namespace.

The endpoint is authoritative for custom keys in opt-in mode. Redirects are rejected, HTTPS is mandatory except on loopback, and key counts/body sizes are bounded. Invalid configuration still stops startup; endpoint failures instead reject unavailable trust at use time. The daemon worker merges concurrent requests and replaces whole snapshots after successful fetches. Defaults: refresh due after 300 seconds, hard age 900 seconds, request cooldown 30 seconds, caller wait 2000 milliseconds. Failed refreshes do not extend age; expired custom keys never fall back to the legacy metadata field. Periodic checks continue after first use even without signature failures. See the JVM property table above for limits.

Legacy metadata works without fetching or periodic refresh. A 1.3.0 agent still has startup-only trust; older agents do not gain multi-key support automatically. Old dummy certificate bypasses remain deliberately removed. Disabled profile-key support returns 204; secure-chat deployments need genuine certificates. Certificate adapters consult live trust at `verify()` time and buffer at most 64 KiB. Velocity's validation entry point rechecks rather than retaining a cached Boolean. This does not retroactively invalidate established sessions.

Clients must load the new agent JAR and backends must implement the endpoint; launcher source changes are unnecessary, while changing only the server cannot update client trust. Old verification APIs remain synchronous: cold/missed checks may wait up to `waitMillis` for network work. Setting it to zero requires callers to retry if keys are not yet available. No universal skin-cache invalidation, automatic repainting, instant revocation, live deployment or full-game acceptance is implied. Retain explicit texture-domain whitelists, overlap planned key rotations and test the actual game/proxy versions.
