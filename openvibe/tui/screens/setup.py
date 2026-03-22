"""First-run setup wizard.

Shown when no model is configured in any config source.  Guides the user
through picking a provider, model, and (optionally) an API key, then writes
the result to ``~/.config/openvibe/openvibe.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (Button, ContentSwitcher, Input, Label, ListItem,
                             ListView, RadioButton, RadioSet, Static)

from openvibe.provider.provider import PROVIDERS, ProviderInfo

_GLOBAL_CONFIG = Path.home() / ".config" / "openvibe" / "openvibe.json"
_CUSTOM_MODEL_ID = "__custom__"

_STEPS = ["provider", "model", "apikey", "confirm"]


class SetupWizardScreen(Screen[None]):
    """Multi-step first-run configuration wizard."""

    DEFAULT_CSS = """
    SetupWizardScreen {
        align: center middle;
        background: #000000;
    }
    #wizard {
        width: 66;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 3;
        overflow-y: auto;
    }
    #wizard-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        margin-bottom: 0;
        height: auto;
    }
    #step-label {
        color: $text-muted;
        text-align: center;
        margin-bottom: 1;
        height: auto;
    }
    #step-scroll {
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }
    ContentSwitcher {
        height: auto;
    }
    .step-heading {
        text-style: bold;
        margin-bottom: 1;
        height: auto;
    }
    .hint {
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
    }
    RadioSet {
        margin-bottom: 1;
        height: auto;
    }
    #model-list {
        height: auto;
        max-height: 10;
        border: solid $border;
        margin-bottom: 1;
    }
    #custom-model-row {
        height: auto;
        margin-bottom: 1;
        display: none;
    }
    #custom-model-label {
        width: 10;
        padding-top: 1;
        height: auto;
    }
    #apikey-input {
        margin-bottom: 1;
    }
    #azure-url-label {
        display: none;
    }
    #azure-url-input {
        display: none;
        margin-bottom: 1;
    }
    #azure-version-label {
        display: none;
    }
    #azure-version-input {
        display: none;
        margin-bottom: 1;
    }
    #conf-url-row {
        display: none;
        height: auto;
        margin-bottom: 0;
    }
    .confirm-row {
        height: auto;
        margin-bottom: 0;
    }
    .confirm-key {
        color: $text-muted;
        width: 14;
        height: auto;
    }
    .confirm-val {
        color: $text;
        height: auto;
    }
    #confirm-path {
        color: $text-muted;
        margin-top: 1;
        height: auto;
    }
    #nav {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    #nav Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "skip", "Skip", show=True),
    ]

    def __init__(self) -> None:
        self._provider: ProviderInfo | None = None
        self._model_id: str | None = (
            None  # litellm model string e.g. "anthropic/claude-sonnet-4-5"
        )
        self._api_key: str = ""
        self._base_url: str = ""  # Azure endpoint URL (required for azure provider)
        self._api_version: str = ""  # Azure API version e.g. "2024-02-01"
        self._step = 0
        # Maps safe widget ID → original litellm model string (slashes are invalid in IDs)
        self._model_id_map: dict[str, str] = {}
        super().__init__()

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="wizard"):
                yield Static("openvibe setup", id="wizard-title")
                yield Static("", id="step-label")

                with VerticalScroll(id="step-scroll"):
                    with ContentSwitcher(initial="provider"):

                        # ── Step 1: Provider ──────────────────────────────
                        with Vertical(id="provider"):
                            yield Static("Choose a provider", classes="step-heading")
                            with RadioSet(id="provider-radio"):
                                for p in PROVIDERS:
                                    yield RadioButton(p.name, id=f"p-{p.id}")

                        # ── Step 2: Model ─────────────────────────────────
                        with Vertical(id="model"):
                            yield Static("Choose a model", classes="step-heading")
                            yield Static("", id="model-hint", classes="hint")
                            yield ListView(id="model-list")
                            with Horizontal(id="custom-model-row"):
                                yield Static("Model string:", id="custom-model-label")
                                yield Input(
                                    placeholder="e.g. my-gpt4o-deployment",
                                    id="custom-model-input",
                                )

                        # ── Step 3: API key ───────────────────────────────
                        with Vertical(id="apikey"):
                            yield Static("API key (optional)", classes="step-heading")
                            yield Static("", id="apikey-provider-hint", classes="hint")
                            yield Input(
                                placeholder="sk-…", password=True, id="apikey-input"
                            )
                            yield Static("", id="apikey-env-hint", classes="hint")
                            # Azure-only: endpoint URL
                            yield Static(
                                "Azure endpoint URL [bold](required)[/bold]",
                                id="azure-url-label",
                                classes="hint",
                            )
                            yield Input(
                                placeholder="https://<resource>.openai.azure.com/",
                                id="azure-url-input",
                            )
                            yield Static(
                                "API version [bold](required)[/bold]",
                                id="azure-version-label",
                                classes="hint",
                            )
                            yield Input(
                                placeholder="e.g. 2024-02-01",
                                id="azure-version-input",
                            )

                        # ── Step 4: Confirm ───────────────────────────────
                        with Vertical(id="confirm"):
                            yield Static("Ready to save", classes="step-heading")
                            with Horizontal(classes="confirm-row"):
                                yield Static("Provider:", classes="confirm-key")
                                yield Static(
                                    "", id="conf-provider", classes="confirm-val"
                                )
                            with Horizontal(classes="confirm-row"):
                                yield Static("Model:", classes="confirm-key")
                                yield Static("", id="conf-model", classes="confirm-val")
                            with Horizontal(classes="confirm-row"):
                                yield Static("API key:", classes="confirm-key")
                                yield Static(
                                    "", id="conf-apikey", classes="confirm-val"
                                )
                            with Horizontal(classes="confirm-row", id="conf-url-row"):
                                yield Static("Endpoint:", classes="confirm-key")
                                yield Static("", id="conf-url", classes="confirm-val")
                            with Horizontal(classes="confirm-row", id="conf-ver-row"):
                                yield Static("API version:", classes="confirm-key")
                                yield Static("", id="conf-ver", classes="confirm-val")
                            yield Static(
                                f"\nWill save to: [dim]{_GLOBAL_CONFIG}[/dim]",
                                id="confirm-path",
                            )

                with Horizontal(id="nav"):
                    yield Button("Back", id="back", variant="default", disabled=True)
                    yield Button("Skip setup", id="skip-btn", variant="default")
                    yield Button("Next →", id="next", variant="primary")

    def on_mount(self) -> None:
        self._refresh_nav()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        btn_id = event.pressed.id or ""
        if btn_id.startswith("p-"):
            pid = btn_id[2:]
            self._provider = next((p for p in PROVIDERS if p.id == pid), None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        custom_row = self.query_one("#custom-model-row")
        if item_id == _CUSTOM_MODEL_ID:
            custom_row.display = True
            self._model_id = None
            self.query_one("#custom-model-input", Input).focus()
        else:
            custom_row.display = False
            # Resolve the safe widget ID back to the original litellm model string
            self._model_id = self._model_id_map.get(item_id, item_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "next":
            self._advance()
        elif event.button.id == "back":
            self._retreat()
        elif event.button.id == "skip-btn":
            self.dismiss(None)

    def action_skip(self) -> None:
        self.dismiss(None)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _advance(self) -> None:
        step = _STEPS[self._step]

        if step == "provider":
            # Auto-select first provider if none explicitly chosen
            if self._provider is None:
                first = next(iter(self.query(RadioButton)), None)
                if first:
                    first.value = True
                    pid = (first.id or "")[2:]
                    self._provider = next((p for p in PROVIDERS if p.id == pid), None)
            self._populate_models()

        elif step == "model":
            # Collect custom string if custom item is selected
            custom_row = self.query_one("#custom-model-row")
            if custom_row.display:
                val = self.query_one("#custom-model-input", Input).value.strip()
                if not val:
                    return  # don't advance without a value
                self._model_id = val
            if not self._model_id:
                return  # nothing selected yet

        elif step == "apikey":
            self._api_key = self.query_one("#apikey-input", Input).value.strip()
            self._base_url = self.query_one("#azure-url-input", Input).value.strip()
            self._api_version = self.query_one(
                "#azure-version-input", Input
            ).value.strip()
            self._populate_confirm()

        elif step == "confirm":
            self._save_and_dismiss()
            return

        self._step = min(self._step + 1, len(_STEPS) - 1)
        self._apply_step()

    def _retreat(self) -> None:
        self._step = max(self._step - 1, 0)
        self._apply_step()

    def _apply_step(self) -> None:
        name = _STEPS[self._step]
        self.query_one(ContentSwitcher).current = name
        self._refresh_nav()
        if name == "apikey":
            self._populate_apikey_hints()

    def _refresh_nav(self) -> None:
        self.query_one("#step-label", Static).update(
            f"Step {self._step + 1} of {len(_STEPS)}"
        )
        self.query_one("#back", Button).disabled = self._step == 0
        next_btn = self.query_one("#next", Button)
        next_btn.label = "Save ✓" if _STEPS[self._step] == "confirm" else "Next →"

    # ------------------------------------------------------------------
    # Step-specific population helpers
    # ------------------------------------------------------------------

    def _populate_models(self) -> None:
        lv = self.query_one("#model-list", ListView)
        lv.clear()
        self.query_one("#custom-model-row").display = False
        self._model_id = None
        self._model_id_map.clear()
        is_azure = self._provider and self._provider.id == "azure"
        hint = self.query_one("#model-hint", Static)
        if is_azure:
            hint.update(
                "[dim]These are common deployment names. Use the custom option "
                "if your deployment has a different name.[/dim]"
            )
            self.query_one("#custom-model-input", Input).placeholder = (
                "e.g. my-gpt4o-deployment"
            )
        else:
            hint.update("")
            self.query_one("#custom-model-input", Input).placeholder = (
                "e.g. anthropic/claude-opus-4-6"
            )
        if self._provider:
            for m in self._provider.models:
                # Widget IDs must be valid CSS identifiers — slashes are not allowed.
                safe_id = m.id.replace("/", "--")
                self._model_id_map[safe_id] = m.id
                lv.append(ListItem(Label(m.name), id=safe_id))
        lv.append(
            ListItem(Label("[dim]Custom model string…[/dim]"), id=_CUSTOM_MODEL_ID)
        )

    def _populate_apikey_hints(self) -> None:
        p = self._provider
        if not p:
            return
        is_azure = p.id == "azure"
        self.query_one("#apikey-provider-hint", Static).update(
            f"Enter your [bold]{p.name}[/bold] API key, or leave blank."
        )
        if p.env_key:
            self.query_one("#apikey-env-hint", Static).update(
                f"[dim]If blank, openvibe will read [bold]{p.env_key}[/bold] from the environment.[/dim]"
            )
        else:
            self.query_one("#apikey-env-hint", Static).update("")
        # Show/hide Azure-specific endpoint and version fields
        for wid in (
            "#azure-url-label",
            "#azure-url-input",
            "#azure-version-label",
            "#azure-version-input",
        ):
            self.query_one(wid).display = is_azure

    def _populate_confirm(self) -> None:
        p = self._provider
        is_azure = p and p.id == "azure"
        self.query_one("#conf-provider", Static).update(p.name if p else "—")
        self.query_one("#conf-model", Static).update(self._model_id or "—")
        if self._api_key:
            self.query_one("#conf-apikey", Static).update("● set (stored in config)")
        elif p and p.env_key:
            self.query_one("#conf-apikey", Static).update(
                f"[dim]not set — will use {p.env_key} env var[/dim]"
            )
        else:
            self.query_one("#conf-apikey", Static).update("[dim]not set[/dim]")
        for row_id in ("#conf-url-row", "#conf-ver-row"):
            self.query_one(row_id).display = bool(is_azure)
        if is_azure:
            self.query_one("#conf-url", Static).update(
                self._base_url or "[dim]not set[/dim]"
            )
            self.query_one("#conf-ver", Static).update(
                self._api_version or "[dim]not set[/dim]"
            )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_and_dismiss(self) -> None:
        if not self._model_id or not self._provider:
            self.dismiss(None)
            return

        parts = self._model_id.split("/", 1)
        provider_id = parts[0] if len(parts) == 2 else self._provider.id
        model_id = parts[1] if len(parts) == 2 else self._model_id

        new_data: dict[str, Any] = {
            "model": {"provider_id": provider_id, "model_id": model_id}
        }
        provider_cfg: dict[str, Any] = {}
        if self._api_key:
            provider_cfg["api_key"] = self._api_key
        if self._base_url:
            provider_cfg["base_url"] = self._base_url
        if provider_cfg:
            new_data["provider"] = {self._provider.id: provider_cfg}

        # Merge into any existing global config
        _GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if _GLOBAL_CONFIG.exists():
            try:
                existing = json.loads(_GLOBAL_CONFIG.read_text())
            except Exception:
                pass

        existing.update(new_data)
        _GLOBAL_CONFIG.write_text(json.dumps(existing, indent=2))
        self.dismiss(None)
