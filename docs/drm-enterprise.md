# Enterprise DRM (§4.4.3, §8.12)

The agreement defines three tiers of video protection:

| Tier | Mechanism | Status |
|---|---|---|
| **MVP** | CloudFront signed cookies + OAC, no public S3 | shipped, default |
| **Enterprise** | MediaPackage DRM via SPEKE + licence server | built to the procurement boundary |
| **Premium** | Forensic watermarking | not started (also vendor-dependent) |

## The procurement boundary

**§1.2 puts "3. parti DRM lisansi ve supply chain" out of scope**, and
that line is exactly where this work stops. It is worth being precise
about what that means, because "DRM is implemented" would be misleading.

**What is built and validated:**

- MediaPackage VOD packaging group and packaging configurations
  (`modules/drm`), producing HLS+FairPlay and DASH+Widevine/PlayReady
- SPEKE wiring: the IAM role MediaPackage assumes to call a key provider,
  with a confused-deputy guard on the account
- Application playback API: token issuance, entitlement enforcement, and
  a licence proxy (`lms/lms/language_platform/drm.py`)
- Configuration surface in LMS Language Settings

**What cannot be built without a purchase:**

- **The licences themselves.** Widevine (Google), PlayReady (Microsoft)
  and FairPlay (Apple) are commercial agreements. In practice you buy
  them bundled through a DRM vendor — Axinom, EZDRM, BuyDRM, Irdeto and
  similar — rather than contracting with all three separately.
- **The SPEKE key provider.** The vendor operates it; `speke_url` is
  where it plugs in. AWS publishes a reference implementation, but a
  reference server issues no valid licences.
- **The FairPlay certificate.** Apple issues this to the account holder
  and it cannot be transferred. Expect this to be the longest lead time.

So enabling DRM is a **contract and a config change**, not a development
project. That is the point of stopping here.

## Why the licence request is proxied

The player never talks to the vendor's licence server directly. It posts
to the platform, which checks entitlement and then forwards the request.

That indirection is the whole security value. Encrypting video is
cheap and, on its own, achieves nothing: if any authenticated user — or
worse, anyone at all — can obtain a licence, DRM becomes an expensive way
to serve public video. The proxy makes the entitlement check
unavoidable.

Three checks run before anything reaches the key server:

1. The playback token is ours (HMAC, constant-time compared) and unexpired.
2. The token belongs to the caller, not someone who was sent it.
3. The caller **still** has access to the lesson.

The third matters more than it looks. Entitlement can be revoked between
issuing a token and using it — a student unenrolled mid-session should
stop being able to renew a licence immediately, not whenever their token
happens to expire.

Playback tokens are deliberately short-lived (default 300 s, clamped to
30–3600). The token is the thing a student could forward to a friend; a
five-minute window bounds that to nearly nothing while still covering a
slow player start.

## Browser/DRM matrix

The three systems are not interchangeable, and picking wrong is
indistinguishable from the video being broken:

| Platform | System | Format |
|---|---|---|
| iOS, iPadOS, tvOS (**any** browser) | FairPlay | HLS |
| macOS Safari | FairPlay | HLS |
| macOS/Windows Chrome, Firefox, Edge | Widevine | DASH |
| Legacy Edge (EdgeHTML) | PlayReady | DASH |
| Unknown | Widevine | DASH |

Two traps are worth knowing, and both are covered by tests:

- **On iOS every browser is WebKit underneath**, so Chrome on an iPhone
  still needs FairPlay.
- **Chrome on macOS advertises both "Macintosh" and "Safari"** in its
  user agent. Matching on the device alone hands it a FairPlay licence
  its Widevine engine cannot use — a bug the test suite caught during
  development.

## Player work still required

The current player is a plain `<video>` element playing progressive MP4.
DRM playback needs **Encrypted Media Extensions**, which that element
does not do on its own. Enabling DRM therefore also requires:

- **Shaka Player** or dash.js, replacing the `<video>` element in
  `VideoBlock.vue` for DRM-enabled lessons
- EME configuration pointing `licence request` at `get_playback_config`'s
  `license_url`, forwarding the `playback_token`
- Keeping the existing player for the MVP tier, since most tenants will
  stay there — the API returns `{"drm": false}` so the frontend can branch
  on one flag

This is deliberately not built yet: the EME integration differs per
vendor (FairPlay in particular needs the vendor's certificate and a
non-standard request shape), so writing it before the vendor is chosen
would mean writing it twice.

## Rollout

1. **Procure** a DRM vendor; obtain the FairPlay certificate (longest
   lead time — start here).
2. **Enable the infrastructure:**
   ```bash
   terraform apply -var enable_drm=true -var speke_url=https://vendor.example/speke/v1
   ```
3. **Configure the app** — LMS Language Settings → DRM: the MediaPackage
   domain (Terraform output `drm.packaging_group_domain_name`), the
   vendor's licence server URL and token.
4. **Repackage content.** Existing videos are progressive MP4 in the VOD
   bucket; DRM playback needs MediaPackage *assets* created from the
   transcoded output. New uploads pick this up automatically; the back
   catalogue needs a one-off pass.
5. **Integrate the player** (see above).
6. **Verify per platform.** Test on a real iPhone, a real Mac and a
   Windows machine. DRM failures are platform-specific by nature, and an
   emulator will not surface a FairPlay certificate problem.

## Cost

DRM adds three recurring costs the Ek-6 model does not include:

- **Vendor subscription** — typically a monthly floor plus per-licence
  fees; the dominant new cost.
- **MediaPackage packaging** — per GB of packaged output, on top of
  existing MediaConvert transcoding.
- **Storage** — packaged HLS *and* DASH renditions alongside the
  originals. Enabling CMAF reduces this where players support it.

Given §4.4.3 places DRM at the Enterprise tier, treat it as a per-tenant
commercial decision rather than a platform default. The MVP tier —
signed cookies, private buckets, no public URLs — already satisfies the
agreement's baseline requirement that video content must never be
publicly reachable.
