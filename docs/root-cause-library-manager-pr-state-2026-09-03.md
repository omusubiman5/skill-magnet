# 原因調査・対応報告: PR未マージをエラー扱いして重複PRを作成した問題

## 結論

画面に出た`Pull request is not merged`は原因ではなく、正常な待機状態を例外へ変換した結果である。根本原因は、Library Managerが`local transaction × remote PR state × side-effect certainty`を一つの状態機械として管理せず、例外発生だけを「復旧」として扱っていたことにある。

## 実際に起きた状態遷移

1. transaction `c5b1b4406d754ee2a4ce48e5c6c3f272`がPR #2を作成した。
2. PR #2はOPENで、正常なmerge待ちだった。
3. アプリがOPENを`Pull request is not merged`例外に変換し、ローカル破棄を提示した。
4. ローカルだけが`abandoned`になり、GitHubのPR #2は残った。
5. 次の操作で新transaction `8dc76704a259400e9b0a2259612155ce`と重複PR #3が作成された。

## 根本原因TREE

```text
PR確認で例外
├─ 直接原因: OPENをMERGED以外の一括エラーにした
├─ 設計原因: 正常待機と障害を分類していなかった
├─ 整合性原因: local破棄とremote PRのライフサイクルを分離した
├─ 冪等性原因: 同一library／remoteの非終端transactionを検索しなかった
└─ UX原因: PRを開く操作とmerge確認を一つのボタン意味へ混在させた
```

## 修正

- OPENは`waiting_for_merge`として返し、例外にしない。
- CLOSED未merge、MERGED、未知状態、digest不一致を別状態にした。
- `GitHubでPRを開く`を独立した唯一の次操作として表示する。
- remote副作用後のlocal-only破棄をGUIとdomain APIの両方で禁止した。
- 同じlibrary／remoteの最新非終端transactionを再利用する。
- 実行中はボタンを無効化して二重操作を拒否する。
- 差分0件はcommit／push／PRを作らず正常終了する。

## 実データの復旧

- 重複PR #2: CLOSED
- 継続対象PR #3: OPEN、MERGEABLE
- transaction `8dc76704a259400e9b0a2259612155ce`: `published_pending / waiting_for_merge`
- PR #3の確認を実コードで実行し、例外、local破棄、新transaction、新PRが発生しないことを確認した。

## 検証

- Library Manager単体試験: 21件PASS
- 実PR #3 state smoke: `OPEN -> waiting_for_merge`
- 全failure tree: [skill-library-manager-failure-tree-2026-09-03.md](skill-library-manager-failure-tree-2026-09-03.md)
