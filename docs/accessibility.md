# Accessibility (§6.3, Ek-4.1)

The agreement asks for three things:

> 6 font kademe: CSS variables ile tum tasarim sistemine uygulanir.
> Akilli tahta modu: buyuk tiklanabilir alanlar, full-screen, minimal UI.
> WCAG 2.1 AA hedef (kontrast, keyboard, focus).

## What a student controls

**Settings → Accessibility** (`/accessibility`), also reachable from the
sidebar. Four independent switches that compose, because "large text and
high contrast and no animation" is a common combination and a bad one to
force a choice between:

| Setting | Options | Effect |
|---|---|---|
| Text size | 6 steps (13–24px) | rescales the whole interface |
| High contrast | on/off | AAA-verified palette |
| Whiteboard mode | on/off | large targets, no hover dependency, less chrome |
| Reduce motion | on/off | removes animation and transitions |

Preferences persist **server-side**, so they follow a student from the
school computer to their phone, and are mirrored to `localStorage` so
they apply on the *first paint* rather than after an API round-trip.
Someone who needs 24px text should never read 16px text while a request
resolves.

## How the font scale reaches the whole design system

§6.3 asks for the scale to apply "tum tasarim sistemine" — to the entire
design system, not to a handful of components. That rules out sizing
components individually, which always leaves something behind.

Instead, one declaration on the root:

```css
html { font-size: var(--lms-font-base); }
```

Tailwind and frappe-ui size everything in `rem`, so this rescales
components, spacing and controls together. A step is just a data
attribute, and `accessibility_rules.py` holds the same six values so the
server and stylesheet cannot drift.

Step 3 (16px) is the default deliberately: the scale shrinks as well as
grows. A control that only enlarges is a zoom button, not an
accessibility feature.

## Contrast is verified, not asserted

"WCAG AA target" is the kind of claim that gets written down and never
checked. Here the contrast maths lives in `accessibility_rules.py` and
the **test suite computes the ratios** of the shipped high-contrast
palette. Changing a colour without meeting the threshold fails a test
rather than surfacing in an audit months later.

Measured ratios of the high-contrast palette (white background):

| Pair | Ratio | AAA needs |
|---|---|---|
| Body text `#111111` | 18.88:1 | 7.0 |
| Muted text `#3d3d3d` | 10.86:1 | 7.0 |
| Links `#0b3d91` | 10.04:1 | 7.0 |
| Error text `#8a0000` | 10.09:1 | 7.0 |
| Success text `#0d4f1c` | 9.76:1 | 7.0 |

High-contrast mode targets **AAA**, not the AA required elsewhere: a
student who switches it on has already told you the default is not
working for them.

Reproduce with `get_contrast_audit` (System Manager) or:

```bash
python -m unittest lms.tests.language_platform.test_accessibility_rules
```

The implementation linearises sRGB channels before weighting them, per
the WCAG formula. The common shortcut — averaging raw channel values —
overstates mid-tone contrast and passes palettes that genuinely fail.

## Whiteboard mode

Ek-4.1 asks for full screen, large tap targets, and no hover-dependent
interaction. That last one is the substantive requirement: **a touch
screen has no hover state**, so a control revealed on hover is not merely
awkward there, it is unreachable. The video player's controls are pinned
open in this mode for exactly that reason.

Targets are 56px rather than the WCAG 2.5.5 minimum of 44px, because a
board is touched from across a classroom rather than held at arm's
length.

## WCAG 2.1 AA: what is covered

Addressed by this work:

- **1.4.3 Contrast (Minimum)** — high-contrast palette verified at AAA
- **1.4.4 Resize Text** — six steps to 24px; no horizontal scroll at the
  largest steps
- **1.4.11 Non-text Contrast** — borders forced to full contrast in
  high-contrast mode, since a vanishing hairline removes structure
- **1.4.1 Use of Colour** — correct/incorrect states get a glyph, not
  only a colour
- **2.3.3 Animation from Interactions** — reduced motion honours both the
  OS setting and an in-app toggle, for shared machines where the OS
  setting is not the student's to change
- **2.4.1 Bypass Blocks** — skip link as the first tab stop
- **2.4.7 Focus Visible** — `:focus-visible` ring, never removed without
  replacement
- **2.5.5 Target Size** — 44px default, 56px on whiteboards

## What is NOT covered — read this before claiming compliance

This work makes the platform *substantially more* accessible and
addresses the specific criteria §6.3 names. It is **not** an audit, and
"WCAG 2.1 AA compliant" is not a claim it supports.

Specifically outstanding:

- **No testing with assistive technology.** Nothing here has been
  exercised with NVDA, JAWS or VoiceOver. Screen-reader flow is the
  largest untested area, and the one where problems hide.
- **No keyboard walkthrough of every screen.** Focus order, focus traps
  in modals, and reachability of every control need a manual pass.
- **frappe-ui's own components are unaudited.** The interface inherits a
  vendor component library; its ARIA semantics and contrast in the
  *default* (non-high-contrast) theme are outside this work.
- **Video captions and transcripts** (Ek-4.1) exist as a data model but
  the player's caption UI has not been accessibility-tested.
- **No automated axe/Lighthouse gate in CI.** Worth adding — it catches
  regressions the unit tests cannot.

A genuine AA conformance statement needs a manual audit against all 50
success criteria, ideally with a disabled user in the loop. The
sensible next step is an axe-core pass in CI plus a screen-reader
walkthrough of the four core journeys: login, lesson playback, exam
taking, and speaking practice.
