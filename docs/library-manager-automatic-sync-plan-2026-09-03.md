# Library Manager自動公開・反映 実装計画

日付: 2026-09-03

## 目的

スキルの登録・更新・削除をローカル管理領域だけで終わらせず、GitHubへのpush、PR作成、自動マージ、merge commit検証、Skill Magnet設定更新、OSメニュー再登録まで一つの操作で完了させる。

## 状態遷移

```text
draft
→ prepared
→ published_pending
→ published_pending（merge要求済みをjournalへ記録）
→ verified
→ active
```

各遷移前後を既存journalへ保存する。PR作成後は同じtransaction、branch、commit、PRを再利用し、二重作成しない。GitHub check待ちは`published_pending`として保持し、同じPRをpollする。権限・policyエラーは無条件retryせず、journalへ失敗段階と原因を保存する。

## 実装項目

1. `LibraryTransaction.merge_pull_request`を追加し、GitHub auto-mergeを一度だけ要求する。
2. `LibraryTransaction.complete_automatically`で既存状態から次の有効遷移を復元してactiveまで進める。
3. GUIの段階ボタンを`GitHubへ反映`へ統合する。
4. 右クリック登録では登録直後に自動transactionを開始する。
5. GUI内の登録・更新・削除も同じ自動transactionを開始する。
6. PR check待ちは15秒間隔で確認し、アプリ再起動時もjournalから再開する。
7. GitHub auto-mergeが無効なrepositoryでは、同じ承認済みPRを即時mergeする。
8. merge要求の重複禁止、全状態遷移、実CMA004公開・メニュー反映を検証する。

## 失敗時の復旧

- publish前失敗: ローカル候補を修正または破棄できる。
- push／PR後失敗: branchとPRを保持し、同じtransactionから再開する。
- merge待ち: merge要求を再送せずPR状態だけ確認する。
- activation失敗: 旧configを復元し、remote commitを`published_but_inactive`として保持する。
- アプリ終了: 次回起動時にjournalから次の有効遷移を復元する。
