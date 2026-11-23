"""Развернуть проектные файлы Codex рядом с исходниками шаблона."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "codex" / "setup"


def main():
    config = (SOURCE / "config.toml").read_text(encoding="utf-8")
    config = config.replace("__PROJECT_ROOT__", json.dumps(str(ROOT), ensure_ascii=False))
    files = {
        ROOT / ".codex/config.toml": config,
        ROOT / ".agents/plugins/marketplace.json": (
            SOURCE / "marketplace.json"
        ).read_text(encoding="utf-8"),
    }
    for agent in sorted((SOURCE / "agents").glob("*.toml")):
        files[ROOT / ".codex/agents" / agent.name] = agent.read_text(encoding="utf-8")

    conflicts = [
        path.relative_to(ROOT)
        for path, content in files.items()
        if path.exists() and path.read_text(encoding="utf-8") != content
    ]
    if conflicts:
        raise SystemExit("Объедините настройки вручную в " + ", ".join(map(str, conflicts)))

    try:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                with path.open("x", encoding="utf-8") as output:
                    output.write(content)
    except OSError as error:
        raise SystemExit(f"Не удалось записать проектные настройки. {error}") from error

    print("Проектные настройки Codex готовы.")


if __name__ == "__main__":
    main()
