"""Шаг 2. Применение перевода к распакованным ресурсам.

Берёт work/text/<таблица>.txt (чистые английские таблицы после 01-extract),
подставляет переводы из data/translations/<таблица>.tsv и data/dictionary.tsv
в целевую колонку (по умолчанию English) и складывает результат в work/ru/.

Что проверяет:
  * какие ключи перевода не нашли строк в новой версии игры (устаревшие);
  * сколько строк осталось без перевода (пишется work/untranslated.txt);
  * покрывают ли шрифты из data/fonts все кириллические символы перевода.

Примеры:
  python 02-apply/apply.py
  python 02-apply/apply.py --work work --strict
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import pipeline  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_name(name):
    out = name
    for ch in '<>:"/\\|?*':
        out = out.replace(ch, "_")
    return out


def main():
    ap = argparse.ArgumentParser(description="Применение перевода к распакованным таблицам")
    ap.add_argument("--work", help="папка с распакованными данными (по умолчанию work)")
    ap.add_argument("--strict", action="store_true", help="завершиться с ошибкой при проблемах с шрифтами")
    args = ap.parse_args()

    cfg = pipeline.load_settings(ROOT)
    work_dir = args.work or os.path.join(ROOT, cfg["work_dir"])
    text_dir = os.path.join(work_dir, "text")
    out_dir = os.path.join(work_dir, "ru")
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.isdir(text_dir):
        raise SystemExit(f"Нет папки {text_dir} — сначала запустите 01-extract")

    version = pipeline.load_work_version(work_dir)
    if version:
        print(f"Версия игры: {version.get('version') or 'не опознана'}"
              + ("" if version.get("known") else " (хеш не из data/versions.json)"))

    dictionary = pipeline.load_dictionary(ROOT)
    blacklist = set(cfg.get("blacklist", []))
    report = io.StringIO()
    untranslated = []
    used_chars = set()
    total_changed = 0

    for fname in sorted(os.listdir(text_dir)):
        if not fname.endswith(".txt"):
            continue
        path = os.path.join(text_dir, fname)
        with io.open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        table = pipeline.parse_table(text)
        if not table:
            continue
        table_name = fname[:-4]
        is_black = table_name in blacklist or any(table_name.startswith(b) for b in blacklist)

        translations = {} if is_black else pipeline.load_translations(ROOT, table_name)
        if is_black:
            changed, stale = 0, 0
        else:
            changed, stale = pipeline.apply_translations(
                table, translations, dictionary, cfg["target_column"])
            if stale:
                report.write(f"{table_name}: ключей перевода без строки в игре — {stale}\n")

        cols = pipeline.target_columns(table, cfg["target_column"])
        translated_cols = {col for _, col in translations if col is not None}
        if translated_cols:
            cols = [c for c in cols if c in translated_cols] or cols
        for cells in table["rows"]:
            if not cells:
                continue
            for col in cols:
                if len(cells) > col:
                    used_chars |= pipeline.cyr_chars(cells[col])
            if is_black or not translations:
                continue
            has_ru = any(len(cells) > col and pipeline.has_cyr(cells[col]) for col in cols)
            if not has_ru:
                sample = cells[cols[0]] if len(cells) > cols[0] else ""
                if sample.strip():
                    untranslated.append(f"{table_name}|{cells[0].strip()}|{sample[:80]}")

        with io.open(os.path.join(out_dir, fname), "w", encoding="utf-8", newline="") as f:
            f.write(pipeline.table_to_text(table))
        total_changed += changed
        note = "" if translations or is_black else " (нет файла перевода — таблица пропущена)"
        report.write(f"{table_name}: заменено ячеек={changed}{note}\n")

    with io.open(os.path.join(work_dir, "untranslated.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(untranslated))

    report.write(f"\nвсего заменено ячеек: {total_changed}\n")
    report.write(f"строк без перевода: {len(untranslated)} (см. work/untranslated.txt)\n")
    report.write(f"уникальных кириллических символов: {len(used_chars)}\n")

    print("Покрытие кириллицы шрифтами из data/fonts:")
    fonts_dir = os.path.join(ROOT, "data", "fonts")
    bad = []
    if os.path.isdir(fonts_dir):
        for name, (ok, total, missing) in pipeline.font_coverage(fonts_dir, used_chars).items():
            line = f"  {name}: {ok}/{total}"
            if missing:
                line += f", нет: {missing}"
                bad.append(name)
            print(line)
            report.write(line + "\n")

    with io.open(os.path.join(work_dir, "apply_report.txt"), "w", encoding="utf-8") as f:
        f.write(report.getvalue())
    print(f"\nТаблицы с переводом: {out_dir}")
    print(f"Отчёт: {os.path.join(work_dir, 'apply_report.txt')}")

    if args.strict and bad:
        raise SystemExit(f"Шрифты не покрывают часть символов: {', '.join(bad)}")


if __name__ == "__main__":
    main()
