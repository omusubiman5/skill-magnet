# Library Manager右クリック連続投入 修正対応報告

## 対応内容

- 右クリック対象の検証・登録をwindow構築前からevent loop開始後へ移した。
- 起動直後に「受付完了」、実行中は具体的な処理名を画面上部へ表示する。
- 処理中は入力欄、参照、登録、CRUD、公開actionを無効化する。
- Library Manager全体へOS file lockを追加し、同一stateで複数processを実行しない。
- 同一folderの再投入は重複として停止し、別folderの投入は並行処理不可と再試行方法を表示する。
- processを強制終了してもOSがlockを解放し、残ったlock fileから再取得できる方式にした。

## 検証基準

| 経路 | 合格条件 |
|---|---|
| 同一processの連打 | 処理中controlがdisabledになり、callbackを再実行しない |
| 同一folderの別process | 2つ目が`already_running`相当となり、`same_request`を識別する |
| 別folderの別process | 2つ目を拒否し、同一folderとは異なる案内を出す |
| 強制終了 | holder processをkill後、次processが同じlockを取得できる |
| 通常終了 | window closeと例外経路の双方でlock handleを解放する |

実行結果、release commit、wheel digest、インストール済み版の確認結果はリリース完了時に本書へ追記する。
