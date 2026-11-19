"""Browser tests for Server settings: the Instructor saves a Limit from the dialog.

A live server opened as ``serve`` opens it, its Settings Store in a temp
dir, driven by headless Chromium.  The dialog is used by keyboard alone
and found by its labels, the way assistive technology reaches it.
"""

import json
import os
import re
import tempfile
from unittest.mock import patch

import pytest

from mniplab.tests.browser import LiveServerCase, Locator, Page, expect
from mniplab.webserver import registry
from mniplab.webserver.config import settings
from mniplab.webserver.logins import LoginStore
from mniplab.webserver.server_settings import LIMITS, SettingsStore

pytestmark = pytest.mark.browser_classroom

#: The idle Terminal timeout's label; its other two uses are behind its ⓘ.
IDLE_TERMINAL = "Idle Terminal timeout"


class ServerSettingsBrowserTests(LiveServerCase):
    """The Instructor opens Server settings from the toolbar and saves a Limit."""

    def setUp(self) -> None:
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store_path = os.path.join(tmp.name, "settings.json")
        for limit in LIMITS:
            self.addCleanup(setattr, settings, limit.name, getattr(settings, limit.name))
        previous = registry.settings_store
        registry.settings_store = SettingsStore(self.store_path, settings)
        self.addCleanup(setattr, registry, "settings_store", previous)
        self.logins_path = os.path.join(tmp.name, "accounts.json")
        self.addCleanup(settings.use_login_store, settings.login_store)
        settings.use_login_store(LoginStore(self.logins_path))

    def open_dialog(self, page: Page) -> None:
        """Open Server settings from the account menu with the keyboard."""
        page.locator("#account-menu-btn").focus()
        page.keyboard.press("Enter")
        item = page.get_by_role("menuitem", name="Server settings", exact=True)
        for _ in range(6):
            if item.evaluate("el => el === document.activeElement"):
                break
            page.keyboard.press("ArrowDown")
        expect(item).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.get_by_role("dialog", name="Server settings")).to_be_visible()

    def open_logins_tab(self, page: Page, name: str = "Accounts") -> Locator:
        """Move from the Limits tab to the logins tab by keyboard; return its panel."""
        dialog = page.get_by_role("dialog", name="Server settings")
        limits = dialog.get_by_role("tab", name="Limits", exact=True)
        expect(limits).to_have_attribute("aria-selected", "true")
        limits.focus()
        page.keyboard.press("ArrowRight")
        tab = dialog.get_by_role("tab", name=re.compile(f"^{name} \\(\\d+\\)$"))
        expect(tab).to_be_focused()
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(dialog.get_by_role("tabpanel", name="Limits")).to_be_hidden()
        return dialog.get_by_role("tabpanel", name=re.compile(f"^{name}"))

    def test_the_instructor_saves_a_limit_by_keyboard_and_it_applies_at_once(self) -> None:
        with self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            dialog = page.get_by_role("dialog", name="Server settings")
            for label in ("Terminals per Session", "Terminals on the whole server",
                          "Idle Session timeout", "Sessions per login"):
                expect(dialog.get_by_label(label, exact=True)).to_be_visible()
            expect(dialog.get_by_label("Sessions per login", exact=True)).to_have_accessible_description(
                re.compile("0 = no cap"))
            # Five fields in Single-Lab Mode: Labs alive at once means nothing here.
            expect(dialog.get_by_role("spinbutton")).to_have_count(5)
            expect(dialog.get_by_label("Labs alive at once")).to_be_hidden()

            field = dialog.get_by_label(IDLE_TERMINAL, exact=True)
            expect(field).to_have_value("300")
            # Its other two uses are there on request, as in Packet Capture.
            about = dialog.get_by_role("button", name="About Idle Terminal timeout", exact=True)
            expect(about).to_have_attribute("aria-expanded", "false")
            about.click()
            expect(about).to_have_attribute("aria-expanded", "true")
            expect(dialog.locator("#limit-terminal_idle_timeout-note")).to_contain_text(
                re.compile("reloaded page.*Packet Capture.*last viewer"))
            field.focus()
            field.fill("30")
            page.keyboard.press("Enter")
            expect(field).to_have_attribute("aria-invalid", "true")
            expect(field).to_have_accessible_description(re.compile("between 60 and 86400"))
            expect(field).to_be_focused()
            self.assertEqual(settings.terminal_idle_timeout, 300)
            self.assertFalse(os.path.exists(self.store_path))

            field.fill("600")
            # Tab from the field reaches the one Save.
            save = dialog.get_by_role("button", name="Save", exact=True)
            for _ in range(10):
                page.keyboard.press("Tab")
                if save.evaluate("el => el === document.activeElement"):
                    break
            expect(save).to_be_focused()
            page.keyboard.press("Enter")
            expect(dialog.get_by_role("tabpanel", name="Limits").get_by_role("status")).to_have_text(
                re.compile("^Saved"))
            expect(field).not_to_have_attribute("aria-invalid", "true")
            self.assertEqual(settings.terminal_idle_timeout, 600)
            with open(self.store_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"terminal_idle_timeout": 600})

            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            expect(page.locator("#account-menu-btn")).to_be_focused()

            self.open_dialog(page)
            expect(dialog.get_by_label(IDLE_TERMINAL, exact=True)).to_have_value("600")

    def test_clearing_a_field_returns_the_limit_to_the_value_the_server_started_with(self) -> None:
        settings.terminal_idle_timeout = 600
        store = registry.settings_store
        assert store is not None
        store.started_with["terminal_idle_timeout"] = 300
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump({"terminal_idle_timeout": 600}, f)
        with self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            dialog = page.get_by_role("dialog", name="Server settings")
            field = dialog.get_by_label(IDLE_TERMINAL, exact=True)
            expect(field).to_have_value("600")
            expect(field).to_have_accessible_description(re.compile("started with 300"))
            expect(dialog.get_by_role("tabpanel", name="Limits")).to_contain_text(
                "Empty a field to go back to the value the server started with")

            field.fill("")
            dialog.get_by_role("button", name="Save", exact=True).click()

            expect(dialog.get_by_role("tabpanel", name="Limits").get_by_role("status")).to_have_text(
                re.compile("^Saved"))
            expect(field).to_have_value("300")
            self.assertEqual(settings.terminal_idle_timeout, 300)
            with open(self.store_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {})

    def test_a_limit_set_where_the_server_was_started_is_shown_fixed(self) -> None:
        with patch.dict(os.environ, {"MNIPLAB_SESSION_IDLE_TIMEOUT": "9000"}), \
                self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            dialog = page.get_by_role("dialog", name="Server settings")

            fixed = dialog.get_by_label("Idle Session timeout", exact=True)
            expect(fixed).to_be_disabled()
            expect(fixed).to_have_accessible_description(re.compile("fixed where the server was started"))
            expect(dialog.get_by_label(IDLE_TERMINAL, exact=True)).to_be_enabled()
            # The seconds also read as people say them.
            expect(dialog.locator("#limit-session_idle_timeout-readable")).to_have_text(" · 2 h 30 min")

    def test_a_cross_field_refusal_is_in_the_dialogs_words(self) -> None:
        with patch.object(settings, "terminal_ping_interval", 100), \
                self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            dialog = page.get_by_role("dialog", name="Server settings")
            field = dialog.get_by_label(IDLE_TERMINAL, exact=True)

            field.fill("100")
            dialog.get_by_role("button", name="Save", exact=True).click()

            expect(field).to_have_accessible_description(
                re.compile("must be at least 101 seconds, so Terminals stay connected")
            )
            self.assertEqual(settings.terminal_idle_timeout, 300)

    def test_focus_stays_in_the_dialog_while_it_is_open(self) -> None:
        with self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            for _ in range(20):
                page.keyboard.press("Tab")
                self.assertTrue(page.evaluate(
                    "() => document.getElementById('server-settings-modal')"
                    ".contains(document.activeElement)"
                ), "Tab left the open dialog")

    def test_multi_lab_mode_offers_labs_alive_at_once(self) -> None:
        with patch.object(settings, "lab_mode", "multi"), \
                self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            dialog = page.get_by_role("dialog", name="Server settings")

            expect(dialog.get_by_role("spinbutton")).to_have_count(6)
            expect(dialog.get_by_label("Labs alive at once", exact=True)).to_have_value(
                str(settings.lab_cap)
            )

    def test_the_instructor_adds_a_learner_login_shows_resets_and_removes_it_by_keyboard(self) -> None:
        with patch.object(settings, "users", "boss:pw12345a:instructor,bob:pw2:learner"), \
                self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            logins = self.open_logins_tab(page)
            # The .env logins: one heading says where they are set, each row only its role.
            env = logins.get_by_role("list", name=re.compile(r"^Set in \.env"))
            expect(env.get_by_role("listitem")).to_have_count(2)
            bob = env.get_by_role("listitem").filter(has_text="bob")
            expect(bob).to_contain_text("Learner")
            expect(bob).not_to_contain_text(".env")
            expect(bob.get_by_role("button")).to_have_count(0)
            expect(logins.get_by_role("list", name=re.compile("^Added here"))).to_be_hidden()

            name = logins.get_by_label("Name of the new login", exact=True)
            expect(name).to_have_accessible_description(re.compile("lowercase letters, digits and hyphens"))
            name.focus()
            page.keyboard.type("Table 3")
            page.keyboard.press("Enter")
            expect(name).to_have_attribute("aria-invalid", "true")
            expect(name).to_have_accessible_description(re.compile("A name is 2 to 32"))
            expect(name).to_be_focused()

            name.fill("bob")
            page.keyboard.press("Enter")
            expect(name).to_have_accessible_description(re.compile("bob is already a login"))

            name.fill("table-3")
            page.keyboard.press("Tab")
            add = logins.get_by_role("button", name="Add login", exact=True)
            expect(add).to_be_focused()
            page.keyboard.press("Enter")

            status = logins.locator("#server-settings-logins-status")
            expect(status).to_contain_text("table-3 can sign in now")
            password = LoginStore(self.logins_path).passwords().get("table-3")
            assert password is not None
            expect(status).to_contain_text(f"with the password {password}")
            expect(name).to_have_value("")
            expect(name).not_to_have_attribute("aria-invalid", "true")
            listed = logins.get_by_role("list", name="Added here (1)")
            added = listed.get_by_role("listitem").filter(has_text="table-3")
            expect(added).to_contain_text(password)
            expect(page.get_by_role("tab", name="Accounts (3)")).to_be_visible()

            # Closed and reopened, the password is hidden until the eye is pressed.
            page.keyboard.press("Escape")
            self.open_dialog(page)
            logins = self.open_logins_tab(page)
            added = logins.get_by_role("listitem").filter(has_text="table-3")
            expect(added).not_to_contain_text(password)
            show = added.get_by_role("button", name="Show password for table-3", exact=True)
            expect(show).to_have_attribute("aria-pressed", "false")
            show.focus()
            page.keyboard.press("Enter")
            expect(added).to_contain_text(password)
            expect(show).to_have_attribute("aria-pressed", "true")
            page.keyboard.press("Enter")
            expect(added).not_to_contain_text(password)
            expect(show).to_have_attribute("aria-pressed", "false")

            # Reset sits in the row's menu and asks first; Cancel, the focused
            # default, changes nothing and returns to the menu's button.
            actions = added.get_by_role("button", name="Actions for table-3", exact=True)
            actions.focus()
            page.keyboard.press("Enter")
            menu = page.get_by_role("menu", name="Actions for table-3")
            expect(menu.get_by_role("menuitem", name="Reset password…")).to_be_focused()
            # Escape closes the menu, not the dialog.
            page.keyboard.press("Escape")
            expect(menu).to_be_hidden()
            expect(page.get_by_role("dialog", name="Server settings")).to_be_visible()
            expect(actions).to_be_focused()
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            confirm = page.get_by_role("dialog", name="Reset password")
            expect(confirm).to_contain_text("table-3")
            expect(confirm.get_by_role("button", name="Cancel", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(confirm).to_be_hidden()
            expect(actions).to_be_focused()
            self.assertEqual(LoginStore(self.logins_path).passwords().get("table-3"), password)

            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.keyboard.press("Tab")
            expect(confirm.get_by_role("button", name="Reset password", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(status).to_contain_text("The password of table-3 was reset")
            renewed = LoginStore(self.logins_path).passwords().get("table-3")
            assert renewed is not None and renewed != password
            expect(status).to_contain_text(f"The new password is {renewed}")
            expect(added).to_contain_text(renewed)
            expect(added).not_to_contain_text(password)
            expect(actions).to_be_focused()

            # Remove asks first too, then the row goes and focus moves to the name field.
            page.keyboard.press("Enter")
            page.keyboard.press("ArrowDown")
            expect(menu.get_by_role("menuitem", name="Remove…")).to_be_focused()
            page.keyboard.press("Enter")
            confirm = page.get_by_role("dialog", name="Remove login")
            expect(confirm).to_contain_text("table-3")
            page.keyboard.press("Tab")
            # Clicked, which also proves the confirmation sits above the dialog.
            confirm.get_by_role("button", name="Remove login", exact=True).click()
            expect(status).to_have_text("Removed table-3.")
            expect(logins.get_by_role("listitem").filter(has_text="table-3")).to_have_count(0)
            expect(env.get_by_role("listitem")).to_have_count(2)
            expect(page.get_by_role("tab", name="Accounts (2)")).to_be_visible()
            expect(name).to_be_focused()
            self.assertEqual(LoginStore(self.logins_path).passwords(), {})

    def test_a_big_class_is_filtered_and_its_env_logins_fold(self) -> None:
        users = ",".join(["boss:pw12345a:instructor"] + [f"learner{i}:pw2:learner" for i in range(1, 26)])
        login_store = settings.login_store
        assert login_store is not None
        for group in ("red", "blue", "green"):
            login_store.add(f"group-{group}", taken=(), by="test")
        with patch.object(settings, "users", users), \
                self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            logins = self.open_logins_tab(page)
            expect(page.get_by_role("tab", name="Accounts (29)")).to_be_visible()
            # The logins added here come first; the 26 from .env fold under one heading.
            expect(logins.get_by_role("list", name="Added here (3)").get_by_role("listitem")).to_have_count(3)
            expect(logins.get_by_role("button", name="Set in .env · change them there (26)", exact=True)).to_be_visible()
            fold = logins.locator("#server-settings-env-toggle")
            expect(fold).to_have_attribute("aria-expanded", "false")
            env = logins.locator("#server-settings-env-list")
            expect(env).to_be_hidden()

            # The filter narrows both lists, says how many it shows, and opens
            # the folded .env logins when a match is there.
            search = logins.get_by_label("Filter logins by name", exact=True)
            search.fill("2")
            expect(logins.locator("#login-filter-count")).to_have_text("8 of 29 shown")
            expect(logins.locator("#server-settings-added-heading")).to_be_hidden()
            expect(fold).to_have_text("Set in .env · change them there (8 of 26)")
            expect(fold).to_have_attribute("aria-expanded", "true")
            expect(env.get_by_role("listitem").filter(visible=True)).to_have_count(8)

            search.fill("blue")
            expect(logins.locator("#login-filter-count")).to_have_text("1 of 29 shown")
            expect(fold).to_be_hidden()

            search.fill("nobody")
            expect(logins).to_contain_text("No login matches “nobody”.")

            search.fill("")
            fold.click()
            expect(env).to_be_visible()
            expect(env.get_by_role("listitem")).to_have_count(26)

            # Reopened, the dialog starts on Limits with the filter empty.
            page.keyboard.press("Escape")
            self.open_dialog(page)
            dialog = page.get_by_role("dialog", name="Server settings")
            expect(dialog.get_by_role("tab", name="Limits", exact=True)).to_have_attribute("aria-selected", "true")
            expect(dialog.locator("#login-filter")).to_have_value("")

    def test_multi_lab_mode_says_each_learner_account_is_a_group(self) -> None:
        with patch.object(settings, "lab_mode", "multi"), \
                self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            panel = self.open_logins_tab(page)

            expect(panel).to_be_visible()
            expect(panel).to_contain_text("Each Learner account is a Group, with its own Lab.")
            dialog = page.get_by_role("dialog", name="Server settings")
            expect(dialog.get_by_role("heading", name="Accounts", exact=True)).to_have_count(1)

    def test_single_lab_mode_says_nothing_of_groups(self) -> None:
        with self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            self.open_dialog(page)
            panel = self.open_logins_tab(page)
            expect(panel.locator("#server-settings-group-note")).to_be_hidden()

    def test_a_learner_is_offered_no_server_settings(self) -> None:
        with self.signed_in_page(role="learner", wait_for_topology=False) as page:
            page.locator("#account-menu-btn").click()
            menu = page.get_by_role("menu")
            expect(menu.get_by_role("menuitem", name="Your Session", exact=False)).to_be_visible()
            expect(menu.get_by_role("menuitem", name="Server settings")).to_have_count(0)
            expect(menu.get_by_role("menuitemcheckbox", name="Sessions & Terminals")).to_have_count(0)

    def test_a_lab_scripts_server_offers_no_server_settings(self) -> None:
        registry.settings_store = None
        with self.signed_in_page(role="instructor", wait_for_topology=False) as page:
            expect(page.get_by_role("link", name="Lab Authoring")).to_be_visible()
            page.locator("#account-menu-btn").click()
            menu = page.get_by_role("menu")
            expect(menu.get_by_role("menuitem", name="Log out")).to_be_visible()
            expect(menu.get_by_role("menuitem", name="Server settings")).to_have_count(0)
