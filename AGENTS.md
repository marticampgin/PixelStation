# Working on Pixel Station

Follow the user's product specification and `docs/ARCHITECTURE.md`. Implement actual behavior and honest provider setup states. No fake production data, inactive controls, placeholder games, marketing slogans, cloud LLM fallback or telemetry.

Keep `IMPLEMENTATION_STATUS.md` accurate. Work in meaningful increments and commit tested changes frequently. Avoid mixing runtime data, OAuth secrets, temporary QA captures or generated private files into commits. Deliberate public UI screenshots for the README belong in `docs/design`. Root/coordinator owns commits when multiple agents share the checkout.

Run backend tests and Ruff plus frontend type/build/tests for affected behavior. Manually verify API and rendered UI paths before reporting a phase complete. Keep deterministic rules, permissions and resource limits in application code. Models provide bounded structured judgment; do not persist chain-of-thought.
