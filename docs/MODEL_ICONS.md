# Model and provider icons

Each model in the chooser and the catalogue shows a small icon. The icon is chosen as follows, first match wins:

1. **An icon uploaded for that model** (Administration → Models → Edit → Icon).
2. **An icon uploaded for its provider** (Administration → Models → Provider icons). It applies to every model
   recognised as that provider.
3. **A lettermark**: two letters on a restrained tone for a recognised provider (for example "Qw" for Qwen), or the
   first letter of the model's name in a plain outlined box.

## Why third-party logos are not included

Semechka is open source and white-label. Model makers' logos are trademarks, and their brand guidelines generally
allow use to refer to the company but not redistribution inside another product's source and packages, or use that
suggests endorsement. Bundling them would push those terms onto every organisation that installs or rebrands
Semechka. So none are shipped, and no logo is ever fetched from another server.

If your organisation has the right to use a provider's logo, upload it under Provider icons. It is stored like your
own branding and served from your server.

## Uploads

Icons go through the same check as logos: the type is detected from the bytes (PNG, WebP, JPEG or SVG), the limit is
512 KB, and an SVG containing scripting, event handlers, external references or embedded documents is refused. Icons
are served from `/api/assets/…` with `X-Content-Type-Options: nosniff`.

## Recognition never grants anything

The provider and family are recognised from the model's id and name (`catalog_svc.PROVIDERS`), and they choose only a
label and an icon:

- A model's capabilities (vision, picture, video) come from what its backend reports, or what an administrator sets
  explicitly.
- Who may use a model comes from its access rules.

A name that looks like a known model therefore never enables a capability or a permission.
