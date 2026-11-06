# Portal Design System

## Direction

The Version 3 portal uses **Signal Blue with warm beige and white surfaces**.
The visual goal is a trustworthy infrastructure product that feels warmer and
more approachable than a generic dark AI dashboard.

The page canvas is warm beige, while cards, forms, tables, dialogs, and the top
application bar remain white. The left navigation is deep navy. Signal Blue is
reserved for primary actions, selected navigation, links, and the main chart
series.

## Core tokens

| Token | Value | Use |
| --- | --- | --- |
| `navy-950` | `#0B1220` | Primary navigation and high-contrast brand surfaces |
| `canvas-warm` | `#F7F2E8` | Portal page background |
| `surface` | `#FFFFFF` | Cards, forms, tables, dialogs, and top bar |
| `text-primary` | `#0F172A` | Headings and primary text |
| `text-secondary` | `#64748B` | Supporting labels and descriptions |
| `border-warm` | `#E7E2D8` | Dividers and boundaries on the warm canvas |
| `signal-blue` | `#2563EB` | Primary actions, links, selection, and main data series |
| `signal-cyan` | `#0891B2` | Secondary data series and routing evidence |
| `success` | `#10B981` | Healthy, complete, and within-budget states |
| `warning` | `#F59E0B` | Degraded, approaching-limit, and review states |
| `danger` | `#DC2626` | Failed, revoked, destructive, and over-limit states |

## Surface hierarchy

1. Use `canvas-warm` for the main page background.
2. Use `surface` for anything the customer reads, edits, compares, or acts on.
3. Use `navy-950` only for persistent navigation and rare high-contrast areas.
4. Avoid beige cards on a beige canvas. White cards provide the working-layer
   contrast and keep dense usage and cost information readable.
5. Keep shadows subtle. Prefer spacing and surface contrast over decorative
   borders.

## Color behavior

- Signal Blue is the only general-purpose action color.
- Cyan is a supporting analytical color, not a competing call-to-action color.
- Green, amber, and red are semantic. Do not use them as decoration.
- Never communicate provider health, budget state, or rollout state with color
  alone. Pair color with text, an icon, or a shape.
- Provider brands do not replace product status colors. Provider identity is a
  label; gateway health and routing state use the portal's semantic colors.
- Intelligent-routing explanations may use a restrained violet accent in future
  experiments, but violet is not part of the base interface palette.

## Typography and density

- Use Inter when bundled; otherwise use the system sans-serif stack.
- Use medium weight for headings and actions and regular weight for body text.
- Use tabular numerals for cost, token, request, budget, and latency values.
- Keep the portal information-dense enough for operators without shrinking body
  text below 14 pixels.
- Prefer short labels and plain-language explanations over technical acronyms.

## Accessibility requirements

- Meet WCAG AA contrast for all text, controls, focus states, and chart labels.
- Preserve a visible keyboard focus indicator on every interactive element.
- Do not use placeholder text as the only field label.
- Give charts a textual summary and expose exact values in accessible tables or
  tooltips.
- Make every important workflow usable at 320 pixels wide and at 200% browser
  zoom.
- Respect reduced-motion preferences and never require animation to understand a
  status change.

## Initial portal shell

The first customer portal navigation should contain:

1. Overview
2. Usage and cost
3. Intelligent routing
4. API keys
5. Budgets
6. Team

The overview should lead with monthly spend, request volume, estimated savings,
and latency. The main analytical area should show cost or request history. A
separate explanation panel should answer why a model was chosen without exposing
the customer's prompt or model response.

## Ownership

This document defines the Version 3 visual baseline. Component implementations
may refine spacing, typography, and responsive behavior, but changes to the core
palette or semantic color meanings should be reviewed as product decisions.
