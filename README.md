# omnipage-wallpapers

Публичный репо с готовыми обоями для расширения [OmniPage](https://github.com/mirakmalsulton/omnipage).
Раздаётся через [jsDelivr CDN](https://www.jsdelivr.com/).

## Как добавить обои

1. Положить оригиналы (JPG/PNG) в категорию:
   ```
   wallpapers-src/<категория>/<имя>.jpg
   ```
   Пример: `wallpapers-src/nature/aurora.jpg`

2. Собрать и запушить:
   ```bash
   python3 tools/build.py --push
   ```

Скрипт:
- жмёт в WebP: `<категория>/<имя>.webp` (full 1920×1080, Q75) + `<имя>_t.webp` (thumb 400×250, Q55)
- обновляет `index.json` (его читает расширение)
- коммитит и пушит в GitHub → jsDelivr подхватывает автоматически (~30 сек)

## Правила именования

- Папка-категория: `nature` → в UI "**Nature**", `pure-dark` → "**Pure Dark**"
- Файл: `aurora.jpg` → "**Aurora**", `city-night.jpg` → "**City Night**"

## Требования

```bash
pip3 install Pillow
```

## CDN URL

```
https://cdn.jsdelivr.net/gh/mirakmalsulton/omnipage-wallpapers@main/index.json
```