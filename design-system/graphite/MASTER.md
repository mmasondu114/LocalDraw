# Local Draw — Warm linen desktop

User-directed replacement of the previous sage palette. Screen-only styling is in the final Warm linen section of web/style.css; print output retains paper surfaces.

## Direction
Warm grey and linen surfaces, muted terracotta primary actions, dark brown text. User requested warmer colors to match their desktop and reduce the uncomfortable contrast with the previous cold palette. No pure-white app surfaces, remote fonts, or global display changes. The focused earth-tone color search informed the brown/amber direction; the application palette reduces saturation for a desktop editor.

## Tokens
| Role | Color |
| --- | --- |
| Canvas | #e7e0d7 |
| Panel | #f3eee7 |
| Raised control | #e9e0d5 |
| Input | #f8f3ec |
| Primary text | #3b3029 |
| Secondary text | #68584c |
| Primary action | #945537 |
| Text on action | #fff7ed |
| Separator | #c9baab |
| Input border | #9a8777 |

## Components
Buttons use 8px corners and 36px minimum desktop height; generation actions use 42px. Secondary controls use neutral surfaces. Destructive actions use muted brick red and quieter placement. Navigation has consistent outline icons and a visible active rail. Fields retain clear boundaries and a 2px keyboard focus outline. Native system fonts keep all content offline.

## Layout
Keep the existing 3440×1440 high-DPI four-column workspace and input persistence. Preview uses a slightly deeper linen surface. Homepage has three task entry cards; only the main drawing action gets the filled accent. Queue, display settings, selected cards, warnings and empty states share the same role colors. Respect reduced motion.

## Verification
Real frontend in isolated WebView2: home, prompt editor, queue, display settings, return-to-editor draft persistence, 375px home, large text at 1024px, and 3440×1440 physical pixels. No live GPU generation requested or performed. The theme is served through the existing style.css route, so the running backend does not need a new asset route. The existing desktop executable is retained.
