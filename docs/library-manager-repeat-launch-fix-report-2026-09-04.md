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

## 自動検証結果

- Library Manager単体: 37件PASS。
- 全suite: 181件PASS、環境依存1件skip。
- 別process競合: 同一folderの2つ目を拒否し、別folderを別要求として拒否。
- 異常終了復旧: lock holderを強制終了後、同じstateとfolderでlock再取得に成功。
- 再現build: 独立した2 wheelの論理payload SHA-256が`f046efc06554f6ca15fce18d8ec924c308f628c7988e6ca09fe6aeee0b1ae05d`で一致。
- release code: `03088d6bd96ecf6a10de19db616bc8d5dcd38452`。

初回merge後の実Windows再導入で、BOM付きの正常な`certificate-state.json`をPythonが拒否する別の再登録阻害を検出した。`utf-8-sig`読込とBOM付き回帰fixtureで修正し、最終release codeを`20050a4eccdbd7215e3dfbf31be90c275452c561`、wheel論理payload SHA-256を`c7afd078594a48b2f4fbcdeab2f4b3717bb4f7b6485b2ae059072438867fc71d`へ更新した。詳細は[Windows右クリックメニュー再登録拒否の原因調査](windows-certificate-bom-root-cause-2026-09-04.md)に記録した。

インストール済みwheelとExplorer context menuの確認結果は、PR merge後の最終導入時に追記する。
