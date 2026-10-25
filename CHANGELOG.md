# Changelog

## 1.4.1（union 联合信任适配）
- 修复：公钥接口（minecraftservices/publickeys）失败或缓存过期时，降级信任集退化为仅内置 Mojang，导致本站签名材质全体不可见。现降级为【元数据 signaturePublickey ∪ Mojang】——本站自身签名的材质在降级期仍可验证，未知密钥依旧拒绝。
- 配套：多站联合（如 swustmc ↔ TUC）场景下，各站使用联盟统一的材质签名密钥时，接口抖动不再造成单侧皮肤批量消失。

## 1.4.0 — local development, unreleased

- Add tag-gated GitHub Actions publishing to YuDream Nexus with separate credential-free build/test and deployment jobs, exact-artifact promotion, release preflight and post-upload SHA-256 verification. Update fork-specific README links and disable inherited upstream deployment jobs on this fork.
- Replace startup-only fetching with lazy activation at first verification or local `/publickeys` access, including when metadata is launcher-prefetched. Startup still validates the flag, endpoint and cache configuration.
- Add a daemon refresh worker, single-flight requests, a shared cooldown, periodic refresh after activation and one bounded wait/re-verification on a signature miss. Default refresh/max-age/cooldown/wait values are 300 s / 900 s / 30 s / 2000 ms; zero wait is supported.
- Atomically replace complete purpose-separated snapshots instead of accumulating removed keys. Reject custom keys after their hard maximum age; failed downloads do not renew that age. Retain the bundled Mojang key, but never fall back to `signaturePublickey` after opt-in.
- Key-endpoint outages or invalid responses now reject unavailable custom trust at use time instead of stopping JVM startup. Still-valid cached keys remain usable until expiry; incompatible `disableHttpd` and invalid configuration still stop startup.
- Make certificate verifier objects consult current trust at verification time, bound their payload buffers to 64 KiB, and bypass Velocity's cached validation result so later checks observe approval and revocation.
- Return current effective keys with `Cache-Control: no-store` from the local public-key endpoint. Leave fixed legacy-metadata mode unchanged.
- Add concurrency, cooldown, timeout, hard-expiry, purpose migration, in-flight revocation and packaged-agent lifecycle tests.

### Runtime-refresh boundaries

Old authlib verification APIs are synchronous: moving HTTP I/O to a worker does not prevent the calling thread from waiting up to `waitMillis`. With zero wait or a slow fetch, callers must retry; this version does not invalidate every game's skin cache or retroactively revoke established sessions. Refresh is polling, not instant remote revocation. Test fixtures are not full Minecraft/Velocity/BungeeCord releases; real two-site acceptance is still required.

## 1.3.0 — local development, unreleased

- Add opt-in `feature.enable_public_key_fetching` and fetch the selected authentication API's `minecraftservices/publickeys` endpoint at startup.
- Keep separate immutable `profilePropertyKeys` and `playerCertificateKeys` trust sets; preserve the bundled Mojang key and legacy metadata behavior when opt-in is absent.
- Use the texture trust set for legacy and modern authlib property verification; expose both sets through the local Minecraft Services public-key endpoint.
- Replace inherited always-success certificate verification in authlib, Velocity and BungeeCord transforms with real RSA verification. Support both BungeeCord player-key package names.
- Return HTTP 204 when player certificate support is disabled, instead of fabricating a key pair with `AA==` signatures.
- Bound public-key responses and RSA key sizes, require HTTPS outside loopback, reject redirects, and stop startup on invalid/unavailable opted-in key responses or incompatible `disableHttpd` settings.
- Add negative signature, transport, purpose-isolation and packaged-agent tests.
- Set the fork's artifact name to `authlib-injector-union`; keep the existing environment version override and local `.dirty` suffix.

### Migration warning

Legacy **metadata** remains compatible. This does **not** preserve the old acceptance of invalid chat certificates: servers requiring secure profiles need genuinely signed certificates and explicitly trusted certificate issuers. See [deployment and trust boundaries](docs/union-public-keys.md).
