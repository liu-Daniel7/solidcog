# Chat Panel Height and Scrolling Design

## Goal

On desktop, keep the right-side local-model chat panel exactly aligned with the combined height of the left-side search and upload panels. Long conversations must scroll inside the message area instead of increasing the workbench height.

## Layout Behavior

Observe the left `.stack` element with `ResizeObserver`. In the desktop two-column layout, apply its measured height to `.chat-panel`. Recalculate when the left stack or viewport size changes.

The chat header and input area remain fixed within the panel. The `.chat-messages` element occupies the remaining height, has `min-height: 0`, and uses vertical overflow scrolling. New messages continue to scroll the message area to its bottom.

At the existing mobile breakpoint, remove the synchronized height and retain the current natural single-column layout. If `ResizeObserver` is unavailable, fall back to natural height without breaking interaction.

## Scope

This change does not alter chat persistence, model calls, uploads, OCR data, or search behavior. OCR search backfilling is explicitly deferred.

## Verification

Verify at desktop and mobile widths that:

- The chat top and bottom align with the left stack on desktop.
- Long chat content does not increase panel height.
- The message region scrolls while the header and input stay visible.
- Resizing the viewport maintains alignment.
- Mobile layout has no overlap, clipping, or forced desktop height.
