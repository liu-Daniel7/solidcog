# Session Chat History Design

## Goal

Preserve the SolidCog assistant conversation when the user opens an OCR view, returns to the workbench, or refreshes the workbench. History lasts only for the current browser tab and is removed automatically when that tab closes.

## State Model

Store a versioned JSON object in `sessionStorage` containing:

- Stable chat messages with sender, text, and metadata text.
- The selected drawing ID and filename.

Do not persist transient loading messages. Rebuild message DOM nodes from structured data instead of restoring saved HTML. If stored JSON is missing, malformed, or uses an unsupported version, discard it and show the default welcome message.

## State Flow

Restore state during workbench initialization before normal interaction begins. Save state after a stable message is added or removed and after drawing selection changes. Selection restoration must use a drawing still present in the server-rendered drawing list; otherwise clear the stale selection while keeping the chat history.

Navigating to `/view-ocr/{id}`, returning to `/home`, or refreshing `/home` must retain both the conversation and valid drawing selection. Closing the browser tab must clear them through normal `sessionStorage` lifetime behavior.

## Clear Control

Add a compact clear-history icon button to the assistant toolbar, after the drawing controls. It has an accessible label and hover tooltip. Clicking it opens a confirmation dialog. Confirmation clears only chat messages, preserves the selected drawing, restores the default welcome message, and immediately saves the new state. Cancellation changes nothing.

The action must never delete drawings, OCR records, uploaded files, or server data.

## Verification

Cover serialization, restoration, malformed-state fallback, exclusion of loading messages, and clear-history behavior with focused frontend tests where supported by the existing test stack. Perform a browser workflow check:

1. Select a drawing and add chat messages.
2. Open its OCR view.
3. Return to the workbench and confirm messages and selection remain.
4. Refresh and confirm they remain.
5. Clear history, cancel once, then confirm once; verify the selection remains and only the welcome message returns.
