#!/usr/bin/env python3
"""
Autonomous Multi-Agent Dashboard (TUI)
Interfaces with agentwork/pipeline.py --json.
"""

import sys
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / "agentwork" / "pipeline.py"


def get_pipeline_data(cmd: list[str]):
    proc = subprocess.run([sys.executable, str(PIPELINE)] + cmd + ["--json"], capture_output=True, text=True)
    if proc.returncode == 0:
        try:
            return json.loads(proc.stdout)
        except Exception as e:
            return {"_error": f"Invalid JSON from pipeline: {e}"}
    return {"_error": proc.stderr.strip() or f"Command {cmd} exited with {proc.returncode}"}


def run_textual_app():
    from textual.app import App, ComposeResult
    from textual.widgets import Header, Footer, DataTable, Static, Log
    from textual import work

    class PipelineTUI(App):
        CSS = """
        DataTable { height: 12; border: solid green; }
        #details-pane { height: 14; border: solid cyan; padding: 1; }
        Log { height: 8; border: solid yellow; }
        """

        BINDINGS = [
            ("n", "next_task", "Next Ready Task"),
            ("d", "drive_all", "Drive All"),
            ("R", "refresh_data", "Refresh"),
            ("q", "quit", "Quit"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield DataTable(id="task_table", cursor_type="row")
            yield Static(id="details-pane")
            yield Log(id="log_pane")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one(DataTable)
            table.add_columns("ID", "Rung", "Status", "Depends")
            self.refresh_table()

        def refresh_table(self) -> None:
            table = self.query_one(DataTable)
            log = self.query_one(Log)
            table.clear()
            tasks = get_pipeline_data(["list"])
            if isinstance(tasks, dict) and "_error" in tasks:
                log.write_line(f"ERROR: {tasks['_error']}")
                return
            for t in (tasks or []):
                deps = ", ".join(t.get("depends", [])) or "—"
                table.add_row(t["id"], str(t["rung"]), t["status"], deps, key=t["id"])
            status = get_pipeline_data(["status"]) or {}
            if "_error" not in status:
                self.title = f"Autonomous Dashboard — {len(status.get('completed', []))} / {status.get('total', 0)} Done"

        def action_next_task(self) -> None:
            nxt = get_pipeline_data(["next"])
            log = self.query_one(Log)
            if isinstance(nxt, dict) and "_error" in nxt:
                log.write_line(f"ERROR: {nxt['_error']}")
            elif nxt and nxt.get("id"):
                log.write_line(f"Next task: {nxt['id']} (Rung {nxt.get('rung')})")
            else:
                log.write_line("No tasks currently ready.")

        @work(thread=True)
        def action_drive_all(self) -> None:
            log = self.query_one(Log)
            self.app.call_from_thread(log.write_line, "Launching autonomous drive...")
            proc = subprocess.Popen(
                [sys.executable, str(PIPELINE), "drive"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if proc.stdout:
                for line in proc.stdout:
                    self.app.call_from_thread(log.write_line, line.rstrip("\n"))
            proc.wait()
            self.app.call_from_thread(log.write_line, f"Drive exited with code {proc.returncode}")
            self.app.call_from_thread(self.refresh_table)

        def action_refresh_data(self) -> None:
            self.refresh_table()

    app = PipelineTUI()
    app.run()


def run_fallback_cli():
    print("=" * 60)
    print(" Autonomous Multi-Agent Dashboard (CLI Mode) ")
    print("=" * 60)
    subprocess.run([sys.executable, str(PIPELINE), "status"])
    print("-" * 60)
    subprocess.run([sys.executable, str(PIPELINE), "list"])
    print("=" * 60)


def main():
    try:
        import textual
        run_textual_app()
    except ImportError:
        run_fallback_cli()


if __name__ == "__main__":
    main()
