# Notes Editor Bigger Visible Area — Spec

**Branch**: `feat/notes-editor-bigger`（chain on `fix/weather-gps-fast-load`）
**Date**: 2026-05-25
**Scope**: 純 CSS 一行修改、無 backend / schema 變動

## 問題

`/notes/<id>/edit` 一般筆記模式（非 checklist）的 textarea `#content-area` 預設 `min-height: 300px`，手機 viewport（iPhone ~700-900px）只佔 1/3，user 一進入就要 scroll 才能看到較多內容。

## 設計

改 `templates/notes/editor.html:11` 一行：

```css
/* before */
#content-area { min-height: 300px; resize: vertical; font-size: 1rem; line-height: 1.6; }
/* after */
#content-area { min-height: 60vh; resize: vertical; font-size: 1rem; line-height: 1.6; }
```

**為何 60vh**：iPhone 14 Pro CSS viewport ~659px、60vh ≈ 395px（比原 300px 大 1.3 倍）；上方 title + store/status/category + editor-toolbar 約 200-250px、剛好讓 user 在一個 viewport 內看到 toolbar + textarea 約 5-7 行內容，不需立刻 scroll。

**保留**：`resize: vertical` — user 仍可手動拉更大。

## 不影響範圍

- Checklist editor（`editor_type == 'checklist'`）不顯示 `#content-area`、完全不受影響
- AI 摘要全螢幕按鈕（line 152-154）不動
- Desktop：60vh 約 600-700px、合理

## 邊界情境

| 情境 | 行為 |
|---|---|
| iOS Safari 含 URL bar 計算 vh | vh = full viewport height (含 URL bar)、跟 user 實際可視略有差距。可接受、min-height 不是嚴格上限 |
| 螢幕極小（<500px）| 60vh = 300px、跟原本一樣、不會變更小 |
| 螢幕極大（4K desktop）| 60vh = 1620px、textarea 變很高；可接受、user 也常想要這樣 |

## Capacity

純 CSS、0 storage / CPU / RAM 影響。

## 測試

CSS 一行改動、無 functional behaviour 變化、既有 editor test 全綠即可。Manual verification：開 `/notes/new?type=note`（或既有 note 的 edit）→ 確認手機/桌面預設 textarea 高度有變大、resize handle 仍可拉動。

## 部署

純 frontend、無 migration、無 dependency 變動。
