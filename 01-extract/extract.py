"""Шаг 1. Распаковка ресурсов игры для перевода.

Что делает:
  * берёт чистый (английский) resources.assets игры — сначала .bak/.orig, потом сам файл;
  * выгружает все текстовые pipe-таблицы в work/text/<таблица>.txt (UTF-8, без изменений);
  * выгружает TrueType-данные шрифтов в work/fonts/ для справки;
  * пишет work/info.txt: таблицы, колонки, число строк, шрифты и покрытие кириллицы;
  * по ключу --import-existing <файл> вытаскивает готовый перевод из уже переведённого
    resources.assets и складывает его в data/translations/<таблица>.tsv (ключ<TAB>перевод).

Примеры:
  python 01-extract/extract.py
  python 01-extract/extract.py --game "D:/Games/Dread Delusion"
  python 01-extract/extract.py --import-existing "game/Dread Delusion_Data/resources.assets"
"""
import argparse
import io
import os
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


def read_assets(env):
    """Path_id -> (имя, текст) для всех TextAsset."""
    result = {}
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        script = getattr(data, "m_Script", b"")
        raw = script.encode("utf-8", "surrogateescape") if isinstance(script, str) else bytes(script)
        result[obj.path_id] = (getattr(data, "m_Name", "?"), raw.decode("utf-8", errors="replace"))
    return result


def import_translations(src_path, column, blacklist, data_dir):
    """Вытаскивает переводы из уже переведённого файла в data/translations."""
    env = UnityPy.load(src_path)
    written = 0
    for _pid, (name, text) in read_assets(env).items():
        if name in blacklist:
            continue
        table = pipeline.parse_table(text)
        if not table:
            continue
        cols = pipeline.target_columns(table, column)
        pairs = {}
        for cells in table["rows"]:
            if not cells:
                continue
            key = cells[0].strip()
            if not key:
                continue
            for col in cols:
                if len(cells) <= col or not pipeline.has_cyr(cells[col]):
                    continue
                if len(cols) == 1:
                    pairs[(key, None)] = cells[col]
                else:
                    pairs[(key, col)] = cells[col]
        if not pairs:
            continue
        path = os.path.join(data_dir, "translations", safe_name(name) + ".tsv")
        pipeline.save_tsv(path, pairs)
        written += len(pairs)
    print(f"  импортировано переводов: {written} (в data/translations)")


def import_dictionary(paths, data_dir):
    """Дополняет data/dictionary.tsv парами «английский=перевод» из файлов словаря."""
    path = os.path.join(data_dir, "dictionary.tsv")
    pairs = {}
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8-sig", errors="replace").read().splitlines():
            if line.startswith("#") or "\t" not in line:
                continue
            key, value = line.split("\t", 1)
            pairs[key.strip()] = value.strip()
    added = 0
    for src in paths:
        if not os.path.exists(src):
            print(f"  ! нет словаря {src}")
            continue
        for line in io.open(src, encoding="utf-8-sig", errors="replace").read().splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and value and key != value and key not in pairs:
                pairs[key] = value
                added += 1
    pipeline.save_tsv(path, pairs)
    print(f"  словарь дополнен: +{added}, всего {len(pairs)}")


def main():
    ap = argparse.ArgumentParser(description="Распаковка ресурсов игры для перевода")
    ap.add_argument("--game", help="папка игры (по умолчанию из data/settings.json)")
    ap.add_argument("--work", help="папка для распакованных данных")
    ap.add_argument("--import-existing", help="уже переведённый resources.assets для импорта строк")
    ap.add_argument("--import-dictionary", nargs="*",
                    help="файлы словаря вида «English=Русский» для data/dictionary.tsv")
    args = ap.parse_args()

    cfg = pipeline.load_settings(ROOT)
    game_dir = args.game or (os.path.normpath(os.path.join(ROOT, cfg["game_dir"]))
                             if cfg.get("game_dir") else None)
    if not game_dir:
        raise SystemExit('Укажите папку игры: --game "<путь к папке с Dread Delusion.exe>"')
    work_dir = args.work or os.path.join(ROOT, cfg["work_dir"])
    text_dir = os.path.join(work_dir, "text")
    fonts_dir = os.path.join(work_dir, "fonts")
    os.makedirs(text_dir, exist_ok=True)
    os.makedirs(fonts_dir, exist_ok=True)

    src = pipeline.pick_clean_assets(game_dir, cfg["assets"])
    if not os.path.exists(src):
        raise SystemExit(f"Не найден файл игры: {src}")
    print(f"Источник: {src}")

    env = UnityPy.load(src)
    info = io.StringIO()
    info.write(f"источник: {src}\n\n")

    tables = 0
    for name, text in read_assets(env).values():
        table = pipeline.parse_table(text)
        if not table:
            continue
        path = os.path.join(text_dir, safe_name(name) + ".txt")
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        tables += 1
        info.write(f"{name}: колонки [{', '.join(c.strip() for c in table['header'])}], "
                   f"строк={len(table['rows'])}\n")
    print(f"Таблиц выгружено: {tables} -> {text_dir}")

    info.write("\nшрифты:\n")
    for obj in env.objects:
        if obj.type.name != "Font":
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name")
        data = tree.get("m_FontData") or b""
        if isinstance(data, list):
            data = bytes(data)
        if isinstance(data, str):
            data = data.encode("latin-1", "replace")
        path = os.path.join(fonts_dir, safe_name(name) + ".ttf")
        with open(path, "wb") as f:
            f.write(data)
        try:
            cmap = pipeline.ttf_cmap(path)
            cyr = sum(1 for c in cmap if 0x0400 <= c <= 0x04FF)
        except Exception as exc:
            cyr = f"ошибка: {exc}"
        info.write(f"  {name}: байт={len(data)}, кириллических символов={cyr}\n")

    print(info.getvalue())

    if args.import_existing:
        print("Импорт перевода из готового файла...")
        import_translations(args.import_existing, cfg["target_column"],
                            set(cfg.get("blacklist", [])), os.path.join(ROOT, "data"))
    if args.import_dictionary:
        print("Импорт словаря...")
        import_dictionary(args.import_dictionary, os.path.join(ROOT, "data"))

    # опознание версии игры по хешу чистого resources.assets
    print("Опознание версии игры...")
    version = pipeline.detect_version(ROOT, src)
    version_path = pipeline.save_work_version(work_dir, version)
    if version["known"]:
        entry = version["entry"] or {}
        print(f"  версия игры: {version['version']}"
              + (f" (Unity {entry['unity']})" if entry.get("unity") else ""))
        if entry.get("note"):
            print(f"  {entry['note']}")
        info_line = f"версия: {version['version']}"
    else:
        hint = pipeline.game_version_hint(game_dir)
        print("  ВНИМАНИЕ: версия игры не опознана по хешу resources.assets.")
        print(f"  sha256: {version['sha256']}")
        print(f"  размер: {version['size']} байт")
        if hint:
            print(f"  подсказка из Game version.txt: {hint}")
        print("  Добавьте хеш в data/versions.json, чтобы версия опознавалась "
              "и к ней можно было привязать отдельные правила.")
        info_line = "версия: не опознана"
    print(f"  записано в {version_path}")

    with io.open(os.path.join(work_dir, "info.txt"), "w", encoding="utf-8") as f:
        f.write(info.getvalue() + f"\n{info_line}\nsha256: {version['sha256']}\n")

    if version["known"]:
        print("Готово.")
    else:
        print("Готово (с предупреждением: версия не опознана).")


if __name__ == "__main__":
    main()
