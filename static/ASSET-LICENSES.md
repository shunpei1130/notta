# 同梱アセットの出典とライセンス（ノッタ v3.29）

本 project's `static/` に同梱した外部アセットの一覧です。実行時の外部通信はありません（すべて同一オリジンから配信）。

| ファイル | 出典 | ライセンス | 備考 |
|---|---|---|---|
| `static/fonts/switzer-400/500/600/700.woff2` | Fontshare（Indian Type Foundry）https://fontshare.com/fonts/switzer | 商用利用可・無料（出典表記は任意） | 画面表示用の英数字書体。日本語は端末内蔵の明朝・ゴシックへフォールバック |
| `static/fonts/sentient-400.woff2` | Fontshare https://fontshare.com/fonts/sentient | 商用利用可・無料（出典表記は任意） | 引用（`.quote`）の欧文書体用 |
| `static/icons/*.webp`（12点） | 3dicons https://3dicons.co | **CC0 1.0**（出典表記不要・改変・商用可） | ナビのステップ目印と空状態アイコン。`static/ASSET-LICENSES.md` 参照 |
| `static/vendor/lottie_light.min.js` | LottieFiles lottie-web（Airbnb/LottieFiles） https://github.com/airbnb/lottie-web | **MIT**（本文: `static/vendor/lottie-LICENSE.txt`） | 確認の落款の再生のみに使用。ライト版（168KB）。落款が必要な画面で**必要時に読み込む**（初回表示ではDLしない） |
| `static/assets/seal.json` | 自作（当プロジェクトで作成） | 当プロジェクト所有 | 落款（朱の枠＋✔）の Lottie データ。AE書き出し形式のキーフレーム |

## 3Dアイコンの一覧（3dicons / CC0）

| id-slug | 用途 |
|---|---|
| `ddbd61-bulb` | ナビ01 目的達成ループ |
| `49b6f4-target` | ナビ02 企画の条件 |
| `628100-notebook` | ナビ03 役割と事例 |
| `744cc0-rocket` | ナビ04 募集ページ／空状態（募集なし） |
| `66b0f8-pencil` | ナビ05 一緒につくる |
| `39121b-medal` | ナビ06 貢献と次の企画 |
| `637858-flash` | ナビ07 AI モデル・処理 |
| `ff5be0-tools` | ナビ08 ツール接続 |
| `176980-folder` | 空状態（企画未選択） |
| `1b714e-tick` | 空状態（確認済み貢献なし） |
| `4f52f8-cube` | 空状態（応募なし） |
| `4a4275-chart` | 予備 |

## 配布時の注意

- 3dicons の一覧ページは `Disallow: /icons/download/` の指定があるため、個別アイコンのWebP（`storage/.../sizes/<id>-<slug>/dynamic/200/color.webp`）を同梱しています。CC0 のため改変・商用利用に制限はありません。
- Fontshare の書体は Latin のみを `unicode-range` で適用しており、日本語は端末内蔵フォントが優先されます。日本語Webフォントの同梱はしていません（可変長テキストのため部分差し込みが実用的でないため）。
- lottie-web は MIT です。NOTICE 全文は `static/vendor/lottie-LICENSE.txt` に保存しています。
