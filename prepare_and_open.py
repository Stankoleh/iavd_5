# -*- coding: utf-8 -*-
"""ЛР5: один запуск із підготовкою шарів та відкриттям ноутбука."""
from __future__ import annotations
import subprocess
import sys
import os
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
DATA = BASE / 'osm_хмельницький'
LAYERS = ['boundary', 'heritage', 'stations', 'amenities', 'road_nodes', 'road_edges']
NB = BASE / 'ЛР5_Станько_Олег_ШІ-33_варіант12.ipynb'


def ready():
    return all((DATA / (name + '.parquet')).exists() and
               (DATA / (name + '.parquet')).stat().st_size > 0 for name in LAYERS)


def run(*args):
    return subprocess.call([sys.executable, *args], cwd=BASE)


def open_notebook():
    print('\nВідкриваю JupyterLab із ноутбуком ЛР5...')
    try:
        return run('-m', 'jupyterlab', str(NB))
    except KeyboardInterrupt:
        return 0


def main():
    print('=' * 64)
    print('ЛР5 • Станько Олег • ШІ-33 • варіант 12 • Хмельницький')
    print('=' * 64)
    print('Python:', sys.executable)
    if ready():
        print('✅ Усі шість GeoParquet-файлів уже на місці.')
        return open_notebook()
    print('\nНемає повного набору даних Хмельницького.')
    print('Спочатку пробую знайти локальний файл OSM XML...')
    from convert_osm_xml_to_lr5 import find_osm_source
    source = find_osm_source()
    if source is not None:
        print('Знайдено OSM XML:', source)
        choice = input('Конвертувати його в GeoParquet? [Enter=так / n=ні]: ').strip().lower()
        if choice not in {'n', 'ні', 'no'}:
            status = run('convert_osm_xml_to_lr5.py', str(source))
            if status == 0 and ready():
                return open_notebook()
            print('Конвертування не завершено. Перевір помилку вище.')
    print("""
Варіанти:
  1 - Відкрити BBBike для завантаження OSM XML (gzip) Хмельницького
      (після завантаження знову запусти START_LR5.bat)
  2 - Спробувати живі запити Overpass (сервери можуть бути зайняті)
  3 - Відкрити ноутбук без даних (для перегляду коду)
  0 - Завершити
""")
    choice = input('Твій вибір [1]: ').strip() or '1'
    if choice == '1':
        webbrowser.open('https://extract.bbbike.org/')
        print('На BBBike обери Khmelnytskyi та формат OSM XML (gzip).')
        print('Потім збережи файл *.osm.gz у цю теку або Downloads.')
        return 0
    if choice == '2':
        status = run('fetch_city_osm.py', 'Хмельницький, Україна', '--no-buildings')
        if status == 0 and ready():
            return open_notebook()
        print('Overpass не повернув повний набір даних. Спробуй BBBike або знімок викладача.')
        return status or 1
    if choice == '3':
        return open_notebook()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
