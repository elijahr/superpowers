#!/usr/bin/env python3
"""
Superpowers Installer TUI

A terminal UI for managing symlinks between ~/Development/superpowers and ~/.claude/
for commands, skills, and agents.

Usage:
    python3 install.py

On first run, creates a venv using uv and installs dependencies.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
VENV_DIR = SCRIPT_DIR / ".venv"
REQUIREMENTS_FILE = SCRIPT_DIR / "requirements.txt"


def ensure_uv() -> str:
    """Ensure uv is available, return path to uv executable."""
    uv_path = shutil.which("uv")
    if uv_path:
        return uv_path

    # Try common locations
    for path in [
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
    ]:
        if path.exists():
            return str(path)

    print("Error: uv not found. Please install uv first:")
    print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
    sys.exit(1)


def ensure_venv(uv: str) -> Path:
    """Ensure venv exists and dependencies are installed, return path to python."""
    venv_python = VENV_DIR / "bin" / "python"

    if not VENV_DIR.exists():
        print("Creating virtual environment with uv...")
        subprocess.run([uv, "venv", str(VENV_DIR)], check=True, cwd=SCRIPT_DIR)

    # Check if we need to install/update dependencies
    marker_file = VENV_DIR / ".requirements_installed"
    requirements_mtime = REQUIREMENTS_FILE.stat().st_mtime if REQUIREMENTS_FILE.exists() else 0
    marker_mtime = marker_file.stat().st_mtime if marker_file.exists() else 0

    if requirements_mtime > marker_mtime:
        print("Installing dependencies with uv...")
        subprocess.run(
            [uv, "pip", "install", "-r", str(REQUIREMENTS_FILE)],
            check=True,
            cwd=SCRIPT_DIR,
            env={**os.environ, "VIRTUAL_ENV": str(VENV_DIR)},
        )
        marker_file.touch()

    return venv_python


def main_bootstrap():
    """Bootstrap: ensure venv and re-exec with venv python if needed."""
    # If we're already in the venv, run the app
    if sys.prefix != sys.base_prefix:
        return run_app()

    # Check if running from the venv python
    venv_python = VENV_DIR / "bin" / "python"
    if Path(sys.executable).resolve() == venv_python.resolve():
        return run_app()

    # Bootstrap: ensure venv and re-exec
    uv = ensure_uv()
    venv_python = ensure_venv(uv)

    # Re-exec with venv python
    os.execv(str(venv_python), [str(venv_python), __file__] + sys.argv[1:])


# =============================================================================
# Main Application (runs inside venv)
# =============================================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ItemStatus(Enum):
    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    CONFLICT = "conflict"  # Exists but wrong symlink or not a symlink


@dataclass
class Item:
    name: str
    category: str  # "commands", "skills", "agents"
    source_path: Path
    dest_path: Path
    status: ItemStatus = ItemStatus.NOT_INSTALLED
    selected: bool = False
    error_message: Optional[str] = None

    def check_status(self) -> None:
        """Check the current installation status of this item."""
        if not self.dest_path.exists() and not self.dest_path.is_symlink():
            self.status = ItemStatus.NOT_INSTALLED
            self.error_message = None
        elif self.dest_path.is_symlink():
            try:
                target = self.dest_path.resolve()
                expected = self.source_path.resolve()
                if target == expected:
                    self.status = ItemStatus.INSTALLED
                    self.error_message = None
                else:
                    self.status = ItemStatus.CONFLICT
                    self.error_message = f"Symlink points to {target}, expected {expected}"
            except Exception as e:
                self.status = ItemStatus.CONFLICT
                self.error_message = str(e)
        else:
            self.status = ItemStatus.CONFLICT
            self.error_message = f"Path exists but is not a symlink: {self.dest_path}"


@dataclass
class Category:
    name: str
    items: list[Item] = field(default_factory=list)

    @property
    def all_installed(self) -> bool:
        return all(item.status == ItemStatus.INSTALLED for item in self.items)

    @property
    def all_selected(self) -> bool:
        return all(item.selected for item in self.items)


def discover_items(source_dir: Path, claude_dir: Path) -> list[Category]:
    """Discover all installable items from the source directory."""
    categories = []

    # Commands: .md files directly in commands/
    commands_src = source_dir / "commands"
    commands_dest = claude_dir / "commands"
    if commands_src.exists():
        items = []
        for f in sorted(commands_src.glob("*.md")):
            name = f.stem
            item = Item(
                name=name,
                category="commands",
                source_path=f,
                dest_path=commands_dest / f.name,
            )
            item.check_status()
            item.selected = item.status == ItemStatus.INSTALLED
            items.append(item)
        if items:
            categories.append(Category(name="commands", items=items))

    # Skills: directories containing SKILL.md
    skills_src = source_dir / "skills"
    skills_dest = claude_dir / "skills"
    if skills_src.exists():
        items = []
        for d in sorted(skills_src.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                name = d.name
                item = Item(
                    name=name,
                    category="skills",
                    source_path=d,
                    dest_path=skills_dest / name,
                )
                item.check_status()
                item.selected = item.status == ItemStatus.INSTALLED
                items.append(item)
        if items:
            categories.append(Category(name="skills", items=items))

    # Agents: .md files directly in agents/
    agents_src = source_dir / "agents"
    agents_dest = claude_dir / "agents"
    if agents_src.exists():
        items = []
        for f in sorted(agents_src.glob("*.md")):
            name = f.stem
            item = Item(
                name=name,
                category="agents",
                source_path=f,
                dest_path=agents_dest / f.name,
            )
            item.check_status()
            item.selected = item.status == ItemStatus.INSTALLED
            items.append(item)
        if items:
            categories.append(Category(name="agents", items=items))

    return categories


@dataclass
class OperationResult:
    item: Item
    action: str  # "install" or "uninstall"
    success: bool
    message: str


def run_app():
    """Run the TUI application."""
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, Tree, Button, Static
    from textual.containers import Container, Horizontal
    from textual.binding import Binding
    from textual import on
    from rich.text import Text

    class SuperpowersInstaller(App):
        """TUI for managing superpowers symlinks."""

        CSS = """
        Screen {
            background: $surface;
        }

        #tree-container {
            height: 1fr;
            border: solid $primary;
            padding: 1;
        }

        #button-container {
            height: 3;
            align: center middle;
        }

        Button {
            margin: 0 2;
        }

        #results-container {
            height: 1fr;
            border: solid $primary;
            padding: 1;
            overflow-y: auto;
        }

        .success {
            color: $success;
        }

        .error {
            color: $error;
        }

        .warning {
            color: $warning;
        }

        #status-line {
            height: 1;
            padding: 0 1;
            background: $primary-background;
        }

        Tree {
            height: 100%;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("space", "toggle_selection", "Toggle", show=False),
            Binding("enter", "apply_changes", "Apply", show=False),
        ]

        def __init__(self, source_dir: Path, claude_dir: Path):
            super().__init__()
            self.source_dir = source_dir
            self.claude_dir = claude_dir
            self.categories: list[Category] = []
            self.results: list[OperationResult] = []
            self.showing_results = False

        def compose(self) -> ComposeResult:
            yield Header()
            yield Container(
                Static("Select items to install/uninstall. [Space] to toggle, [Enter] or Install button to apply.", id="status-line"),
                Container(id="tree-container"),
                Horizontal(
                    Button("Install/Uninstall", id="apply-btn", variant="primary"),
                    Button("Refresh", id="refresh-btn"),
                    Button("Quit", id="quit-btn", variant="error"),
                    id="button-container",
                ),
                Container(id="results-container"),
            )
            yield Footer()

        def on_mount(self) -> None:
            self.refresh_tree()

        def refresh_tree(self) -> None:
            """Refresh the tree view with current state."""
            self.categories = discover_items(self.source_dir, self.claude_dir)

            tree_container = self.query_one("#tree-container")
            tree_container.remove_children()

            tree = Tree("Superpowers")
            tree.root.expand()

            for category in self.categories:
                cat_label = self._make_category_label(category)
                cat_node = tree.root.add(cat_label, data={"type": "category", "category": category})
                cat_node.expand()

                for item in category.items:
                    item_label = self._make_item_label(item)
                    cat_node.add_leaf(item_label, data={"type": "item", "item": item})

            tree_container.mount(tree)

        def _make_category_label(self, category: Category) -> Text:
            """Create a label for a category node."""
            checkbox = "[x]" if category.all_selected else "[ ]"
            return Text(f"{checkbox} {category.name}")

        def _make_item_label(self, item: Item) -> Text:
            """Create a label for an item node."""
            checkbox = "[x]" if item.selected else "[ ]"

            if item.status == ItemStatus.INSTALLED:
                status_icon = "✓"
                style = "green"
            elif item.status == ItemStatus.CONFLICT:
                status_icon = "⚠"
                style = "yellow"
            else:
                status_icon = " "
                style = "white"

            text = Text()
            text.append(f"{checkbox} ")
            text.append(f"{status_icon} ", style=style)
            text.append(item.name)

            if item.status == ItemStatus.CONFLICT and item.error_message:
                text.append(f" ({item.error_message[:40]}...)" if len(item.error_message) > 40 else f" ({item.error_message})", style="dim")

            return text

        def _update_tree_display(self) -> None:
            """Update the tree display after selection changes."""
            try:
                tree = self.query_one(Tree)
                for cat_node in tree.root.children:
                    if cat_node.data and cat_node.data.get("type") == "category":
                        category = cat_node.data["category"]
                        cat_node.set_label(self._make_category_label(category))

                        for item_node in cat_node.children:
                            if item_node.data and item_node.data.get("type") == "item":
                                item = item_node.data["item"]
                                item_node.set_label(self._make_item_label(item))
            except Exception:
                pass

        @on(Tree.NodeSelected)
        def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            """Handle node selection (toggle on click)."""
            self._toggle_node(event.node)

        def _toggle_node(self, node) -> None:
            """Toggle selection for a node."""
            if not node.data:
                return

            if node.data.get("type") == "category":
                category = node.data["category"]
                # Toggle all items in category
                new_state = not category.all_selected
                for item in category.items:
                    item.selected = new_state
            elif node.data.get("type") == "item":
                item = node.data["item"]
                item.selected = not item.selected

            self._update_tree_display()

        def action_toggle_selection(self) -> None:
            """Toggle selection with space key."""
            try:
                tree = self.query_one(Tree)
                if tree.cursor_node:
                    self._toggle_node(tree.cursor_node)
            except Exception:
                pass

        @on(Button.Pressed, "#apply-btn")
        def on_apply_pressed(self) -> None:
            self.apply_changes()

        @on(Button.Pressed, "#refresh-btn")
        def on_refresh_pressed(self) -> None:
            self.refresh_tree()
            self.show_results([])

        @on(Button.Pressed, "#quit-btn")
        def on_quit_pressed(self) -> None:
            self.exit()

        def action_apply_changes(self) -> None:
            self.apply_changes()

        def apply_changes(self) -> None:
            """Apply the selected changes."""
            results: list[OperationResult] = []

            for category in self.categories:
                # Ensure destination directory exists
                if category.name == "commands":
                    dest_dir = self.claude_dir / "commands"
                elif category.name == "skills":
                    dest_dir = self.claude_dir / "skills"
                elif category.name == "agents":
                    dest_dir = self.claude_dir / "agents"
                else:
                    continue

                for item in category.items:
                    if item.selected and item.status != ItemStatus.INSTALLED:
                        # Install
                        result = self._install_item(item, dest_dir)
                        results.append(result)
                    elif not item.selected and item.status == ItemStatus.INSTALLED:
                        # Uninstall
                        result = self._uninstall_item(item)
                        results.append(result)
                    elif item.selected and item.status == ItemStatus.CONFLICT:
                        # Conflict - cannot install
                        results.append(OperationResult(
                            item=item,
                            action="install",
                            success=False,
                            message=f"Cannot install: {item.error_message}",
                        ))
                    elif not item.selected and item.status == ItemStatus.CONFLICT:
                        # Conflict - cannot uninstall
                        results.append(OperationResult(
                            item=item,
                            action="uninstall",
                            success=False,
                            message=f"Cannot uninstall: {item.error_message}",
                        ))

            # Refresh status after changes
            for category in self.categories:
                for item in category.items:
                    item.check_status()
                    # Update selection to match new status
                    item.selected = item.status == ItemStatus.INSTALLED

            self._update_tree_display()
            self.show_results(results)

        def _install_item(self, item: Item, dest_dir: Path) -> OperationResult:
            """Install an item by creating a symlink."""
            try:
                # Ensure destination directory exists
                dest_dir.mkdir(parents=True, exist_ok=True)

                if item.dest_path.exists() or item.dest_path.is_symlink():
                    return OperationResult(
                        item=item,
                        action="install",
                        success=False,
                        message=f"Destination already exists: {item.dest_path}",
                    )

                item.dest_path.symlink_to(item.source_path)
                return OperationResult(
                    item=item,
                    action="install",
                    success=True,
                    message=f"Created symlink: {item.dest_path} -> {item.source_path}",
                )
            except Exception as e:
                return OperationResult(
                    item=item,
                    action="install",
                    success=False,
                    message=str(e),
                )

        def _uninstall_item(self, item: Item) -> OperationResult:
            """Uninstall an item by removing the symlink."""
            try:
                if not item.dest_path.is_symlink():
                    return OperationResult(
                        item=item,
                        action="uninstall",
                        success=False,
                        message=f"Not a symlink: {item.dest_path}",
                    )

                target = item.dest_path.resolve()
                expected = item.source_path.resolve()
                if target != expected:
                    return OperationResult(
                        item=item,
                        action="uninstall",
                        success=False,
                        message=f"Symlink points elsewhere: {target}",
                    )

                item.dest_path.unlink()
                return OperationResult(
                    item=item,
                    action="uninstall",
                    success=True,
                    message=f"Removed symlink: {item.dest_path}",
                )
            except Exception as e:
                return OperationResult(
                    item=item,
                    action="uninstall",
                    success=False,
                    message=str(e),
                )

        def show_results(self, results: list[OperationResult]) -> None:
            """Show operation results in the results container."""
            results_container = self.query_one("#results-container")
            results_container.remove_children()

            if not results:
                results_container.mount(Static("No operations performed.", classes=""))
                return

            # Build results tree
            tree = Tree("Results")
            tree.root.expand()

            successes = [r for r in results if r.success]
            failures = [r for r in results if not r.success]

            if successes:
                success_node = tree.root.add(Text(f"✓ Success ({len(successes)})", style="green"))
                success_node.expand()
                for r in successes:
                    success_node.add_leaf(Text(f"{r.action}: {r.item.category}/{r.item.name}", style="green"))

            if failures:
                failure_node = tree.root.add(Text(f"✗ Errors ({len(failures)})", style="red"))
                failure_node.expand()
                for r in failures:
                    failure_node.add_leaf(Text(f"{r.action}: {r.item.category}/{r.item.name} - {r.message}", style="red"))

            results_container.mount(tree)

    # Run the app
    source_dir = SCRIPT_DIR
    claude_dir = Path.home() / ".claude"

    if not source_dir.exists():
        print(f"Error: Source directory does not exist: {source_dir}")
        sys.exit(1)

    if not claude_dir.exists():
        print(f"Error: Claude directory does not exist: {claude_dir}")
        sys.exit(1)

    app = SuperpowersInstaller(source_dir, claude_dir)
    app.run()


if __name__ == "__main__":
    main_bootstrap()
