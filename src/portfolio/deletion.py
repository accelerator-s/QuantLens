"""Permanently remove account data without creating recovery copies."""
import json
import shutil

import yaml

from paths import Workspace
from .models import Account


def _contains_account(value, account_id):
    if isinstance(value, dict):
        return account_id in value or any(_contains_account(item, account_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_account(item, account_id) for item in value)
    return isinstance(value, str) and (value == account_id or value.startswith(f"portfolio://{account_id}/"))


def delete_account_data(ws: Workspace, account: Account) -> None:
    from automation import AutomationStore
    from lifecycle.store import LifecycleStore
    from trash import TrashStore

    account_id = account.account_id
    directory = ws.portfolio_account(account.market, account.instrument_class, account_id)
    automation = AutomationStore(ws.config / "automation.yaml")
    config = automation.load()
    if account_id in config.account_ids:
        remaining = [value for value in config.account_ids if value != account_id]
        automation.save(config.model_copy(update={"account_ids": remaining,
                                                  "enabled": config.enabled and bool(remaining)}))

    if ws.lifecycle_db.exists():
        LifecycleStore(ws.lifecycle_db).purge_account(account_id)

    # Old entry/undo transactions can contain account files or shared index/config copies.
    for manifest in (ws.portfolio_root / ".transactions").glob("*/state.json"):
        state = json.loads(manifest.read_text(encoding="utf-8"))
        matches = False
        for item in state["files"]:
            target = ws.root / item["path"]
            if target == directory or directory in target.parents:
                matches = True
                break
            backup = manifest.parent / item["copy"]
            if target.suffix in {".yaml", ".json"} and backup.exists():
                if _contains_account(yaml.safe_load(backup.read_text(encoding="utf-8")), account_id):
                    matches = True
                    break
        if matches:
            shutil.rmtree(manifest.parent)

    # Remove older trash copies of the same account as well, if its ID was reused.
    trash = TrashStore(ws)
    for entry in trash.list(category="portfolio_account"):
        if entry.original_path == str(directory.resolve()):
            trash.purge(entry.bucket_dir.name, confirm=True)

    shutil.rmtree(directory)
