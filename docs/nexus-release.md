# Nexus 自动发布

本仓库发布 Maven 坐标 `online.yudream:authlib-injector-union:X.Y.Z`，主 JAR 是可直接用于 `-javaagent` 的 shaded agent，不是未打包依赖的普通 JAR；同时发布源码 JAR、POM 和 Maven 校验文件。

目标仓库固定为 `https://nexus.yudream.online/repository/maven-releases/`。`#admin/security/users` 是管理页面，不能作为上传地址。此功能不向 Maven Central 或上游下载站发布，也不自动创建 GitHub Release。

## 一次性配置

在 **mcyudream/authlib-injector-union → Settings → Secrets and variables → Actions** 配置：

| Secret | 用途 |
|---|---|
| `NEXUS_USERNAME` | 有目标 Maven 仓库发布权限的 CI 账号 |
| `NEXUS_PASSWORD` | 对应密码或 Nexus 用户令牌密码 |

不要将实际凭据写入 README、工作流、Gradle 配置或命令行历史。流水线使用 GitHub 内置的只读 `GITHUB_TOKEN`，无需把个人 GitHub API token 存入仓库 Secrets。构建/测试 job 没有 Nexus 凭据，凭据只注入发布 job 的网络步骤。

Nexus 账号应只获准目标仓库的 browse/read/add 和发布所需 edit 权限，不需要管理员权限。正式仓库建议采用 **Disable redeploy / ALLOW_ONCE**；流水线也会预检 JAR、源码 JAR、POM 是否存在，但无法代替服务端的不可覆盖策略。不要为了让 CI 成功而放开管理权限或允许覆盖旧版本。

若仓库是新 fork，还需在 GitHub Actions 页面启用工作流。本分支的构建与 Nexus 发布工作流使用的 action 均固定到 commit SHA。继承的上游 release/wiki 工作流增加了仓库身份门禁，不会在本 fork 向上游发布。

## 触发与版本规则

- 普通分支 push、PR、手动运行 `CI`：构建、Java 测试、发布工具测试、产物检查并保存 Actions artifact，不发布 Nexus。
- 推送 `vX.Y.Z` 标签：运行 `Publish to Nexus`。标签必须与 `gradle.properties` 的 `version` 完全一致，指向当前干净提交；禁止 `.dirty`、SNAPSHOT、前导零和额外版本后缀。
- 手动重跑发布流程：必须选择同一个有效版本标签；在普通分支运行不会发布。已有任何主产物的版本会被拒绝，不能用重跑覆盖。
- 已发布标签不得移动。改过源码就升版本、建新标签。1.4.0 尚未发布时，README 中的 1.4.0 下载地址可能返回 404；CI 配置完成不等于版本已经上线。

正式发版前同步 README 的开发标记和 CHANGELOG 的发布状态，并将这些修改包含在发布提交中；CI 不会替维护者修改源码或推送提交。维护者确认提交已推送并且版本准备就绪后，才执行例如：

```sh
git tag -a v1.4.0 -m "发布 authlib-injector-union 1.4.0"
git push origin v1.4.0
```

工作流校验标签与版本，不会自己创建或移动标签；默认不发布每次开发提交。

## 流水线验证

1. 使用 JDK 17 / Gradle 8.0.2，Gradle 二进制分发包校验 SHA-256。
2. 验证版本标签，运行发布工具单测，以及全部 Java 单测和独立 JVM 的 agent 测试。
3. 生成 agent、源码 JAR、POM；检查 manifest 版本、Git 提交、Java 8 字节码、ASM relocation、测试夹具未入包。POM 不声明已打包的运行依赖。
4. 保存含 `SHA256SUMS` 的构建产物。独立发布 job 下载并核对这些产物，检查 POM 重生成结果一致；上传时跳过 JAR 重建，发布的就是测试过的文件。
5. 只把 Nexus 对三个待发布文件的 **404** 解释为尚未发布；200、401、403、429、重定向或服务故障都停止发布。使用 HTTPS，不跳过证书校验；预检与下载校验不会携带凭据跟随重定向。
6. 使用 Gradle Maven Publish 上传并维护 Maven metadata，最后下载三个远端文件，逐一比对 SHA-256，成功后在 Actions Summary 给出下载地址和主 JAR 哈希。

上传是多个 Maven HTTP 请求，**不是跨文件原子事务**。网络故障可能留下部分文件；此时不要删除/覆盖已发布内容来强行重跑，应检查 Nexus 状态，修复故障并按新版本发布。也不要将“构建成功”当成“上传成功”：以下载校验步骤完成为准。

## 下载与使用

版本 `1.4.0` 首次发布成功后的地址：

```text
https://nexus.yudream.online/repository/maven-releases/online/yudream/authlib-injector-union/1.4.0/authlib-injector-union-1.4.0.jar
```

使用 Actions Summary 中的 SHA-256 或同目录 Maven 校验文件验证下载，然后保持原参数格式：

```text
-javaagent:/absolute/path/authlib-injector-union-1.4.0.jar=https://your-auth-site.example/api/yggdrasil/
```

仍须确认启动器实际加载这个 agent，不会下载原版覆盖它；认证站接口、材质域名和多公钥安全边界见[联合公钥协议](union-public-keys.md)。发布校验不代表两站实机验收已经完成。

## 本地检查（不上传）

```sh
python3 -m unittest discover -s ci/tests -v
./gradlew --no-daemon test shadowJar sourcesJar generatePomFileForAgentPublication
python3 ci/release.py bundle
python3 ci/release.py verify
```

生成 `build/release/` 的产物与校验文件。若该目录含其他版本的旧 bundle，工具会拒绝混包；先将旧目录移走保留，再为新版本打包。

## English summary

Push an immutable `vX.Y.Z` tag matching `gradle.properties` to publish the tested shaded agent, sources and POM to `online.yudream:authlib-injector-union:X.Y.Z` in the Nexus `maven-releases` repository. Ordinary pushes and PRs only build/test. Configure `NEXUS_USERNAME` and `NEXUS_PASSWORD` as Actions secrets; a personal GitHub token is not used by the workflows. Build and publish run in separate jobs, and JARs are not rebuilt during upload. Existing files, authentication failures and redirects fail the preflight; the remote bytes are downloaded and SHA-256 checked after Maven publication. Maven uploads are not atomic: do not overwrite partially published versions. This setup does not publish to upstream or create GitHub Releases, and does not imply that version 1.4.0 is already available.
