# MechVL Balanced Inference Design

## Problem

On the RTX 4060 Laptop GPU, the current MechVL request can exceed SolidCog's 180-second timeout. The request uses an image up to 2048 by 2048 pixels and permits 512 generated tokens. The server continues inference after the Windows client times out, leaving the model busy.

## Design

Use a balanced local-inference profile:

- Limit the MechVL preview to 1024 by 1024 pixels.
- Limit generation to 128 new tokens.
- Allow SolidCog to wait up to 600 seconds.

The existing OCR context remains part of the prompt, preserving dimension and title-block text that may be reduced by image downscaling. Request locking, error handling, model quantization, and Qwen OCR behavior remain unchanged.

## Configuration

Set `MECHVL_TIMEOUT_SECONDS=600` in the SolidCog environment and document the same default in `.env.example`. Set the MechVL server's default `MECHVL_MAX_NEW_TOKENS` to 128 while retaining the environment-variable override.

## Verification

Run the focused service tests, restart both services, verify both health endpoints, and submit one real drawing-analysis request. The request must complete without a client timeout and the model must return to `busy: false` afterward.
