"""Generate bench/tasks/*.json (24 tasks, 6 families x 4 variants).

Each task names its app (bench/app.py FAMILY PARAM), the title the oracle
expects, the v1 arm (MCP calls; "{var}" placeholders are filled from earlier
`capture` regexes), and the legacy arm (screenshot -> click/type at the
oracle-given widget geometry -> screenshot, on the user's seat, the way a
pixel-grounded harness operates).
"""

from __future__ import annotations

import json
from pathlib import Path

TASKS = Path(__file__).resolve().parent / "tasks"


def q(name: str, role: str | None = None, **extra) -> dict:
    query = {"name": name, "match": "exact", **extra}
    if role:
        query["role"] = role
    return {"query": query}


def wait_title(expect: str) -> dict:
    return {"tool": "wait_for", "args": {"condition": {"window": {"title": expect, "match": "exact"}},
                                         "timeout_ms": 5000}}


def run(steps: list[dict]) -> dict:
    return {"tool": "run", "args": {"steps": steps}}


def forms() -> list[dict]:
    out = []
    for i, (name, email, sub) in enumerate([("Ada", "ada@example.org", True), ("Linus", "linus@kernel.org", False),
                                            ("Grace", "grace@navy.mil", True), ("Ken", "ken@bell-labs.com", False)]):
        expect = f"B:done {name}|{email}|{int(sub)}"
        steps = [{"tool": "act", "args": {"target": q("Name"), "action": "set_text", "text": name}},
                 {"tool": "act", "args": {"target": q("Email"), "action": "set_text", "text": email}}]
        legacy = [{"op": "click", "target": "Name"}, {"op": "type", "text": name},
                  {"op": "click", "target": "Email"}, {"op": "type", "text": email}]
        if sub:
            steps.append({"tool": "act", "args": {"target": q("Subscribe"), "action": "toggle"}})
            legacy.append({"op": "click", "target": "Subscribe"})
        steps += [{"tool": "act", "args": {"target": q("Submit"), "action": "press"}}, wait_title(expect)]
        legacy.append({"op": "click", "target": "Submit"})
        goal = (f"Fill the Name field with '{name}' and the Email field with '{email}', "
                + ("tick the Subscribe checkbox, " if sub else "leave Subscribe unticked, ") + "then click Submit.")
        out.append({"id": f"forms-{i + 1}", "family": "forms", "param": "-", "expect": expect, "goal": goal,
                    "v1": [run(steps)], "legacy": legacy})
    return out


def dialogs() -> list[dict]:
    out = []
    for i, (file, choice) in enumerate([("report.pdf", "Delete"), ("notes.txt", "Keep"), ("photo.png", "Delete"),
                                        ("draft.odt", "Keep")]):
        expect = "B:done " + ("deleted" if choice == "Delete" else "kept")
        steps = [{"tool": "act", "args": {"target": q(f"Delete {file}"), "action": "press"}},
                 {"tool": "wait_for", "args": {"condition": {"element": {"name": choice, "match": "exact",
                                                                         "role": "button"}},
                                               "timeout_ms": 5000}},
                 {"tool": "act", "args": {"target": q(choice, "button"), "action": "press"}}, wait_title(expect)]
        legacy = [{"op": "click", "target": f"Delete {file}"}, {"op": "click", "target": choice}]
        goal = f"Click 'Delete {file}' and answer the confirmation dialog with '{choice}'."
        out.append({"id": f"dialogs-{i + 1}", "family": "dialogs", "param": file, "expect": expect, "goal": goal,
                    "v1": [run(steps)], "legacy": legacy})
    return out


def menus() -> list[dict]:
    out = []
    for i, (top, item) in enumerate([("File", "Export PDF"), ("File", "Export PNG"), ("File", "Open"),
                                     ("Edit", "Preferences")]):
        expect = f"B:done {item}"
        steps = [{"tool": "act", "args": {"target": q(top), "action": "press"}},
                 {"tool": "act", "args": {"target": q(item), "action": "press"}}, wait_title(expect)]
        legacy = [{"op": "click", "target": top}, {"op": "click", "target": item}]
        goal = f"Choose {top} > {item} from the window's menu bar."
        out.append({"id": f"menus-{i + 1}", "family": "menus", "param": "-", "expect": expect, "goal": goal,
                    "v1": [run(steps)], "legacy": legacy})
    return out


def lists() -> list[dict]:
    out = []
    for i, n in enumerate([3, 17, 29, 38]):
        expect = f"B:done Item {n}"
        steps = [{"tool": "act", "args": {"target": q(f"Item {n}"), "action": "select"}},
                 {"tool": "act", "args": {"target": q("Open"), "action": "press"}}, wait_title(expect)]
        legacy = [{"op": "scroll_to", "target": f"Item {n}", "viewport": "List"},
                  {"op": "click", "target": f"Item {n}"}, {"op": "click", "target": "Open"}]
        goal = f"Select 'Item {n}' in the list (scroll if needed), then click Open."
        out.append({"id": f"lists-{i + 1}", "family": "lists", "param": "-", "expect": expect, "goal": goal,
                    "v1": [run(steps)], "legacy": legacy})
    return out


def multiwindow() -> list[dict]:
    out = []
    for i, code in enumerate(["4821", "7305", "1166", "9902"]):
        expect = f"B:done {code}"
        v1 = [{"tool": "observe", "args": {"scope": "visible", "query": {"name": "Transfer code"}},
               "capture": {"code": r"Transfer code: (\d+)"}},
              run([{"tool": "act", "args": {"target": q("Code"), "action": "set_text", "text": "{code}"}},
                   {"tool": "act", "args": {"target": q("Apply"), "action": "press"}}, wait_title(expect)])]
        legacy = [{"op": "click", "target": "Code"}, {"op": "type", "text": code},
                  {"op": "click", "target": "Apply"}]
        goal = ("A second window titled 'Source' shows a transfer code. Enter that code into the Code field "
                "of the window titled 'B:', then click Apply.")
        out.append({"id": f"multiwindow-{i + 1}", "family": "multiwindow", "param": code, "expect": expect,
                    "goal": goal,
                    "v1": v1, "legacy": legacy})
    return out


def text() -> list[dict]:
    out = []
    for i, word in enumerate(["GOLF", "CHARLIE", "INDIA", "ECHO"]):
        expect = f"B:done {word}"
        v1 = [{"tool": "act", "args": {"target": {"query": {"text": word, "match": "exact"}}, "action": "click"}},
              wait_title(expect)]
        legacy = [{"op": "click_word", "target": word}]
        goal = f"The window shows nine words drawn on a canvas. Click the word {word}."
        out.append({"id": f"text-{i + 1}", "family": "text", "param": word, "expect": expect, "goal": goal, "v1": v1,
                    "legacy": legacy})
    return out


def main() -> None:
    TASKS.mkdir(exist_ok=True)
    for old in TASKS.glob("*.json"):
        old.unlink()
    tasks = forms() + dialogs() + menus() + lists() + multiwindow() + text()
    for task in tasks:
        (TASKS / f"{task['id']}.json").write_text(json.dumps(task, indent=1) + "\n", encoding="utf-8")
    print(f"{len(tasks)} tasks")


if __name__ == "__main__":
    main()
