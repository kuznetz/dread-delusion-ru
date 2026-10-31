"""Шаг 3. Сборка готовых переведённых файлов игры.

Берёт чистый resources.assets игры, подставляет переведённые таблицы из work/ru/,
вшивает шрифты с кириллицей из data/fonts/ (Alagard, Romulus, PerfectDOSVGA437),
переводит TMP-шрифт в динамический режим с дополнительным местом в атласе
и собирает подписи меню (menuText_en.json).

Результат складывается в out/Dread Delusion_Data/ — эту папку можно копировать
в корень игры.

Примеры:
  python 03-generate/generate.py --game "D:/Games/Dread Delusion"
  python 03-generate/generate.py --game "D:/Games/Dread Delusion" --out "D:/build/rus"
"""
import argparse
import io
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import UnityPy  # noqa: E402

from common import pipeline  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_name(name):
    out = name
    for ch in '<>:"/\\|?*':
        out = out.replace(ch, "_")
    return out


def patch_text_assets(env, ru_dir):
    """Подставляет переведённые таблицы в TextAsset."""
    patched, missing = 0, []
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        name = getattr(data, "m_Name", "?")
        path = os.path.join(ru_dir, safe_name(name) + ".txt")
        if not os.path.exists(path):
            missing.append(name)
            continue
        with io.open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        tree = obj.read_typetree()
        tree["m_Script"] = text
        obj.save_typetree(tree)
        patched += 1
    return patched, missing


def build_menu_text(game_dir, ru_dir, menu_cfg, out_game_dir):
    """Подписи меню: ключи берём из переведённой generalData."""
    src = os.path.join(game_dir, menu_cfg["source"])
    if not os.path.exists(src):
        return None
    table_path = os.path.join(ru_dir, safe_name(menu_cfg["table"]) + ".txt")
    ru = {}
    if os.path.exists(table_path):
        with io.open(table_path, encoding="utf-8", errors="replace") as f:
            table = pipeline.parse_table(f.read())
        if table:
            cols = pipeline.target_columns(
                table, pipeline.load_settings(ROOT)["target_column"])
            for cells in table["rows"]:
                if not cells:
                    continue
                key = cells[0].strip()
                if not key:
                    continue
                for col in cols:
                    if len(cells) > col and pipeline.has_cyr(cells[col]):
                        ru[key] = cells[col]
                        break
    with io.open(src, encoding="utf-8-sig") as f:
        data = json.load(f)
    changed = 0
    for item in data.get("menuTextItems", []):
        value = ru.get(item.get("key"))
        if value:
            item["value"] = value
            changed += 1
    dst_dir = os.path.join(out_game_dir, "StreamingAssets", "MenuText")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(menu_cfg["source"]))
    with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return changed


def main():
    ap = argparse.ArgumentParser(description="Сборка переведённых файлов игры")
    ap.add_argument("--game", help="папка игры (по умолчанию из data/settings.json)")
    ap.add_argument("--work", help="папка с распакованными данными (по умолчанию work)")
    ap.add_argument("--out", help="куда собрать готовую папку Dread Delusion_Data")
    args = ap.parse_args()

    cfg = pipeline.load_settings(ROOT)
    game_dir = args.game or (os.path.normpath(os.path.join(ROOT, cfg["game_dir"]))
                             if cfg.get("game_dir") else None)
    if not game_dir:
        raise SystemExit('Укажите папку игры: --game "<путь к папке с Dread Delusion.exe>"')
    work_dir = args.work or os.path.join(ROOT, cfg["work_dir"])
    out_dir = args.out or os.path.join(ROOT, cfg["out_dir"])
    ru_dir = os.path.join(work_dir, "ru")
    if not os.path.isdir(ru_dir):
        raise SystemExit(f"Нет папки {ru_dir} — сначала запустите 02-apply")

    version = pipeline.load_work_version(work_dir)
    version_label = (version or {}).get("version") or "не опознана"
    print(f"Версия игры: {version_label}"
          + ("" if (version or {}).get("known") else " (хеш не из data/versions.json)"))

    src = pipeline.pick_clean_assets(game_dir, cfg["assets"])
    print(f"Источник: {src}")
    env = UnityPy.load(src)

    patched, missing = patch_text_assets(env, ru_dir)
    print(f"Таблиц подставлено: {patched}" + (f", без перевода: {len(missing)}" if missing else ""))

    font_ids, font_patch = pipeline.patch_fonts(env, ROOT, cfg["fonts"])
    for name, size in font_patch:
        print(f"  шрифт {name}: TTF заменён на {size} байт")

    print("TMP-шрифты:")
    tmp_result = pipeline.patch_tmp(env, cfg["tmp"], font_ids)
    if tmp_result:
        enlarged, results = tmp_result
        for name in enlarged:
            print(f"  атлас {name}: увеличен до {cfg['tmp']['atlas_size']}x{cfg['tmp']['atlas_size']}")
        for name, info in results:
            print(f"  {name}: {info}")

    os.makedirs(out_dir, exist_ok=True)
    for _fname, fitem in env.files.items():
        fitem.mark_changed()
    env.save(pack="none", out_path=out_dir)
    for old, new in pipeline.normalize_output(out_dir, src):
        print(f"  {old} -> {new}")

    out_game_dir = os.path.join(out_dir, "Dread Delusion_Data")
    os.makedirs(out_game_dir, exist_ok=True)
    built = os.path.join(out_dir, "resources.assets")
    if os.path.exists(built):
        shutil.move(built, os.path.join(out_game_dir, "resources.assets"))
    for extra in ("resources.assets.resS", "resources.resource"):
        path = os.path.join(out_dir, extra)
        if os.path.exists(path):
            shutil.move(path, os.path.join(out_game_dir, extra))

    changed = build_menu_text(game_dir, ru_dir, cfg["menu_text"], out_game_dir)
    if changed is not None:
        print(f"  menuText: переведено ключей {changed}")

    # проверка результата
    verify = io.StringIO()
    result_path = os.path.join(out_game_dir, "resources.assets")
    env2 = UnityPy.load(result_path)
    used = set()
    for obj in env2.objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        script = getattr(data, "m_Script", b"")
        raw = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else bytes(script)
        used |= pipeline.cyr_chars(raw.decode("utf-8", errors="replace"))
    verify.write(f"версия игры: {version_label}\n")
    verify.write(f"файл: {result_path}\nкириллических символов в тексте: {len(used)}\n")
    fonts_dir = os.path.join(ROOT, "data", "fonts")
    for name, (ok, total, bad) in pipeline.font_coverage(fonts_dir, used).items():
        verify.write(f"  {name}: покрыто {ok}/{total}" + (f", НЕТ: {bad}" if bad else "") + "\n")
    with io.open(os.path.join(out_dir, "verify.txt"), "w", encoding="utf-8") as f:
        f.write(verify.getvalue())
    print(verify.getvalue())
    print(f"Готово: {out_game_dir}")


if __name__ == "__main__":
    main()
