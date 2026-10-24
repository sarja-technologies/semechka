# Model and developer icons

Each model in the picker and the catalogue shows a small icon. The icon is chosen as follows, first match wins:

1. **An icon uploaded for that model** (Administration → Models → Edit → Icon).
2. **An icon uploaded for its developer** (Administration → Models → Developer icons). It applies to every model
   recognised as that developer.
3. **The developer's logo**, when Semechka bundles one (see below) and **Developer logos** is on
   (`models.developer_logos`, on by default).
4. **A lettermark**: two letters on a restrained tone for a recognised developer (for example "Qw" for Qwen), or the
   first letter of the model's name in a plain outlined box.

No icon is ever fetched from another server.

## Which logos are bundled, and why only these

From 0.4.0 Semechka bundles a developer's logo only when that developer's published terms allow a mark to be used to
identify its own model without asking first. The marks live in `web/static/model-logos/`, outside the Apache-2.0
licence, with their own `NOTICE.md` (sources, drawing licences, owners' terms). They were checked on 2026-10-25
against each developer's own site, Hugging Face organisation and GitHub organisation.

| Developer | Shown as | Why |
|---|---|---|
| Qwen (Alibaba) | Logo (blue mark; one-colour on dark surfaces) | Matches qwen.ai and the QwenLM / Qwen organisations |
| Wan, Z-Image (Alibaba Tongyi Lab) | Logo (Tongyi mark) | The mark both teams use on Hugging Face and GitHub |
| Nous Research | Logo | Matches nousresearch.com |
| Black Forest Labs | Logo | Owner's file from bfl.ai/brand |
| Tencent Hunyuan | Logo | Owner's file; Tencent is not affiliated with Semechka |
| LTX (Lightricks) | Logo (wordmark) | Owner's file from ltx.io |
| MiniMax | Logo | Owner's file from its GitHub repository |
| Mistral AI | Logo (unaltered colours) | Its brand page welcomes careful use and forbids recolouring |
| DeepSeek | Logo | Matches its Hugging Face and GitHub organisations |
| OpenAI, Meta, Microsoft, NVIDIA, Stability AI, Google | Lettermark | Their brand terms require permission before using the logo |
| Dolphin AI, huihui.ai, HauhauCS | Lettermark | No vector logo published (or none at all) |

If your organisation has the right to use a logo that is not bundled, upload it under Developer icons. It is stored
like your own branding and served from your server.

## Developer, not packager or runtime

The developer is whoever made the model. Accounts that re-package a model (for example `unsloth`, `bartowski`,
`ggml-org`, `mradermacher`) and the runtime that serves it (llama.cpp, diffusers, Ollama) are never shown as its
developer. For a community fine-tune the picker shows the fine-tuner as the developer and, when the model's name
says so, the family it is built on ("huihui.ai, based on Qwen"). Base families are never shown with their logo as if
they had made the fine-tune.

## Uploads

Icons go through the same check as logos: the type is detected from the bytes (PNG, WebP, JPEG or SVG), the limit is
512 KB, and an SVG containing scripting, event handlers, external references or embedded documents is refused. Icons
are served from `/api/assets/…` with `X-Content-Type-Options: nosniff`.

## Recognition never grants anything

The developer and family are recognised from the model's identifier and the name its engine reports
(`catalog_svc.PROVIDERS`), never from an administrator's alias, and they choose only a label and an icon:

- A model's capabilities (vision, picture, video) come from what its backend reports, or what an administrator sets
  explicitly.
- Who may use a model comes from its access rules.

A name that looks like a known model therefore never enables a capability or a permission.
