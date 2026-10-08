# RebelSnatch design system

Rules for any UI change. If a change breaks a rule here, change the change, not the rule. Every token lives in `olemiss_snatch/static/app.css` under `:root`. Use the variables, never raw hex values in templates.

## Color: one dominant, one accent, neutrals

| Role | Token | Hex | Use it for |
|---|---|---|---|
| Dominant | `--navy` | `#14213D` | Headings, wordmark, primary buttons, links, checked switches, focus rings |
| Dominant hover | `--navy-hover` | `#0C1528` | Hover and active state of navy elements, nothing else |
| Accent | `--red` | `#CE1126` | Only for **scarcity**: a full section's seat count, the "full" count in search, error toasts. Never for decoration, borders, buttons or headings |
| Accent tint | `--red-tint` | `#FCEBED` | Background behind a full seat count, nothing else |
| Surface | `--white` | `#FFFFFF` | The content surface: main panel, inputs, tables |
| Background | `--bg` | `#FAFAFA` | The page behind surfaces |
| Line | `--line` | `#E8E8E8` | 1px hairlines: dividers, table rules, input borders |
| Text | `--ink` | `#171717` | Body text |
| Muted text | `--muted` | `#6B6B6B` | Secondary text: times, CRNs, captions |
| Subtle fill | `--fill` | `#F2F2F2` | Hovered rows, the closed-section label, the "you" row in Trades |

- Open seats aren't green. They're `--ink` at weight 600. Full is the only state that gets color, so the eye lands on scarcity.
- No other colors. That rules out green, blue and purple. The only exception is the multicolor Google "G", which Google's branding rules require on the sign-in button.
- Bootstrap's semantic colors (`text-success`, `btn-danger`, `text-bg-*`, `alert-*` tints) are banned in templates. Flash messages are restyled in `app.css` to fit the palette.

## Type

- **One family: Urbanist**, loaded from Google Fonts at weights 400, 500, 600 and 700. Don't add Inter, system sans or a second display face.
- Numbers in tables use `font-variant-numeric: tabular-nums`.
- Scale. Pick from it and don't add sizes:

| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `--fs-display` | 56px / 64px (40px / 48px under 576px) | 700 | Landing headline only |
| `--fs-h1` | 32px / 40px | 700 | One per page: the page's focal heading |
| `--fs-h2` | 20px / 32px | 600 | Section headings |
| `--fs-body` | 16px / 24px | 400 | Body |
| `--fs-small` | 14px / 24px | 400–500 | Table cells, captions, meta |
| `--fs-label` | 12px / 16px, uppercase, `letter-spacing: .06em` | 600 | Table headers and eyebrow labels only |

## Spacing: strict 8px grid

- Every margin, padding, gap and fixed height is a multiple of 8: `--s1` 8px, `--s2` 16px, `--s3` 24px, `--s4` 32px, `--s6` 48px, `--s8` 64px, `--s12` 96px.
- With Bootstrap utilities, use only `*-2` (8px), `*-3` (16px), `*-4` (24px) and `*-5` (48px). **Never `*-1`** (4px).
- Controls are 40px tall (48px for the landing CTA). Table rows have 16px vertical padding.
- Between sections on a page, leave 48px or more of whitespace, not a box.

## Layout and hierarchy

- Each page has **one focal point**, and it's the first thing in the reading order:
  - **Landing:** the headline and the single sign-in button.
  - **Dashboard:** the My Subscriptions table.
  - **Course:** the sections table.
  - **Stats:** the four totals.
- Everything else is visibly secondary: smaller headings, muted text, below the fold or in a narrower column.
- **One white surface per page**, sitting on `--bg`, with a 1px `--line` border and 8px radius. Inside it, separate groups with whitespace and hairline dividers. **Never nest cards**, and don't wrap single items in their own box.
- Max content width is 1200px. Text blocks are 640px or narrower.

## No random decoration

These are forbidden:
- gradients (linear, radial or mesh)
- glows, colored shadows or `box-shadow` of any kind. Elevation comes from the `--line` border only. The one exception is the 1px navy keyboard focus ring, which is there for accessibility.
- rotated or tilted elements, glassmorphism and `backdrop-filter`
- sparkle, lightning, "magic" or other decorative icons. That includes icon tiles above feature text and icons in headings.
- feature grids of three identical icon-plus-blurb columns
- floating or pulsing badges, emoji in UI copy
- pill badges as decoration. A pill shape is only allowed on the seat count, where it carries meaning.
- stock illustrations

Icons are allowed only when they replace a word that doesn't fit and the meaning is unambiguous, and they must be monochrome in `currentColor`. Right now the UI uses none. Text links say what they do ("Register in Experience ↗").

## Real assets

- The landing preview is the **actual sections table**, rendered from live seat data (`db.showcase_course`) with the same markup and CSS as the course page. Don't replace it with a mock-up or illustration.
- Screenshots in docs or marketing come from the running app (Playwright), never from a design tool.

## Components (all in `app.css`)

- `.btn-primary-ink`: the primary action, navy fill with white text. At most one per view.
- `.btn-quiet`: secondary actions, white with a `--line` border and `--ink` text.
- `.seat`: seat count. `.seat.full` uses the red tint, `.seat.open` is plain ink at weight 600, `.seat.closed` is muted on `--fill`.
- `.data-table`: 12px uppercase muted headers, hairline row rules, no zebra stripes, no outer border (the surface provides it).
- `.panel`: the one white surface.
- `.section-gap`: 48px top margin and a hairline above. Use it for secondary groups inside a panel.

## Hooks that must not change

`static/app.js` and the tests depend on these:
- IDs: `search-input`, `search-results`, `term-select`, `enroll-*`, `open-to-trade`, `leave-trades`, `phone-*`, `alert-email-*`, `block-*`, `snatch-toast`
- Classes: `snatch-switch`, `trade-want`, `leave-trades-btn`, `admin-clear`, `admin-unblock`
- Attributes: `data-crn`, `data-term`, `data-watchers-crn`, `data-remove-row`

Restyle these freely, but don't rename them.
