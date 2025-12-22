import json
from pathlib import Path
from typing import Any, Dict, Union, List


Json = Union[dict, list, str, int, float, bool, None]


def _type_name(x: Any) -> str:
    return type(x).__name__


def _is_list_of_str(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def validate_tarot_structure(
    data: Dict[str, Any],
    *,
    allow_extra_keys: bool = True
) -> Dict[str, Any]:

    report = {
        "ok": True,
        "missing": [],
        "type_errors": [],
        "extra_keys": []
    }

    def fail():
        report["ok"] = False

    def missing(path: str):
        fail()
        report["missing"].append(path)

    def type_error(path: str, expected: str, got: Any):
        fail()
        report["type_errors"].append({
            "path": path,
            "expected": expected,
            "got": _type_name(got)
        })

    def extra(path: str):
        fail()
        report["extra_keys"].append(path)

    # ---------- top level ----------
    if not isinstance(data, dict):
        type_error("$", "dict", data)
        return report

    for k in ["id", "name_zh", "name_en", "summary", "meanings"]:
        if k not in data:
            missing(k)

    if "id" in data and not isinstance(data["id"], str):
        type_error("id", "str", data["id"])
    if "name_zh" in data and not isinstance(data["name_zh"], str):
        type_error("name_zh", "str", data["name_zh"])
    if "name_en" in data and not isinstance(data["name_en"], str):
        type_error("name_en", "str", data["name_en"])

    # ---------- summary ----------
    summary = data.get("summary")
    if not isinstance(summary, dict):
        type_error("summary", "dict", summary)
        return report

    for k in ["card_story_zh", "core_upright_zh", "core_reversed_zh"]:
        if k not in summary:
            missing(f"summary.{k}")

    if "card_story_zh" in summary and not _is_list_of_str(summary["card_story_zh"]):
        type_error("summary.card_story_zh", "list[str]", summary["card_story_zh"])

    for k in ["core_upright_zh", "core_reversed_zh"]:
        if k in summary and not isinstance(summary[k], str):
            type_error(f"summary.{k}", "str", summary[k])

    # ---------- meanings ----------
    meanings = data.get("meanings")
    if not isinstance(meanings, dict):
        type_error("meanings", "dict", meanings)
        return report

    for polarity in ["upright", "reversed"]:
        if polarity not in meanings:
            missing(f"meanings.{polarity}")
            continue

        block = meanings[polarity]
        if not isinstance(block, dict):
            type_error(f"meanings.{polarity}", "dict", block)
            continue

        for k in ["general_zh", "career_zh", "love_zh", "money_zh", "short_zh"]:
            if k not in block:
                missing(f"meanings.{polarity}.{k}")

        for k in ["general_zh", "career_zh", "love_zh", "money_zh"]:
            if k in block and not _is_list_of_str(block[k]):
                type_error(
                    f"meanings.{polarity}.{k}",
                    "list[str]",
                    block[k]
                )

        short = block.get("short_zh")
        if not isinstance(short, dict):
            type_error(f"meanings.{polarity}.short_zh", "dict", short)
            continue

        for k in ["love_zh", "career_zh", "money_zh"]:
            if k not in short:
                missing(f"meanings.{polarity}.short_zh.{k}")
            elif not isinstance(short[k], str):
                type_error(
                    f"meanings.{polarity}.short_zh.{k}",
                    "str",
                    short[k]
                )

        if not allow_extra_keys:
            allowed = {"general_zh", "career_zh", "love_zh", "money_zh", "short_zh"}
            for k in block:
                if k not in allowed:
                    extra(f"meanings.{polarity}.{k}")

    return report


def main():
    base_dir = Path(__file__).parent
    json_files = sorted(base_dir.glob("*.json"))

    if not json_files:
        print("⚠️ No json files found.")
        return

    total = len(json_files)
    failed = 0

    for path in json_files:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"\n❌ {path.name}")
            print(f"    JSON load error: {e}")
            failed += 1
            continue

        report = validate_tarot_structure(data, allow_extra_keys=True)

        if report["ok"]:
            print(f"✅ {path.name}")
        else:
            failed += 1
            print(f"\n❌ {path.name}")

            if report["missing"]:
                print("  Missing:")
                for m in report["missing"]:
                    print(f"    - {m}")

            if report["type_errors"]:
                print("  Type errors:")
                for t in report["type_errors"]:
                    print(
                        f"    - {t['path']} "
                        f"(expected {t['expected']}, got {t['got']})"
                    )

            if report["extra_keys"]:
                print("  Extra keys:")
                for e in report["extra_keys"]:
                    print(f"    - {e}")

    print("\n========================")
    print(f"Total files : {total}")
    print(f"Failed      : {failed}")
    print(f"Passed      : {total - failed}")
    print("========================")


if __name__ == "__main__":
    main()
