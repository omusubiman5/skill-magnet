---
artifact: edge-cases
version: "1.0"
created: 2026-09-03
status: active
---

# Edge Cases: Skill Library Manager publish transaction

## Feature Overview

作成済みskillをローカル管理libraryへ登録し、隔離preview、GitHub branch／PR、merge確認、Skill Magnet有効化までを一つの永続transactionとして扱う。分析単位は例外クラスではなく、`local state × remote state × user action × side-effect certainty`である。

**Related Documents:**

- [要件定義](skill-library-management-requirements.md)
- [WinError 5原因調査](root-cause-library-manager-winerror5-2026-09-03.md)
- [中断復旧報告](library-manager-recovery-implementation-report-2026-09-03.md)

## Failure Tree

```text
操作結果
├─ 正常完了
├─ 正常待機（エラーではない）
│  ├─ PR OPEN → GitHubでのmerge待ち
│  ├─ 利用者が確認ダイアログを取消 → 現状態を保持
│  └─ CI／review待ち → PRを開く／後で再確認
├─ 利用者が修正できる入力不備
│  ├─ URL空／不正
│  ├─ SKILL.md不足／catalog不整合
│  └─ 変更0件
├─ 再試行可能な実行障害
│  ├─ network timeout／GitHub 5xx
│  ├─ Windows file lock／permission denied
│  └─ Git／gh一時失敗
├─ remote状態競合
│  ├─ PR CLOSED未merge
│  ├─ branch head変更
│  ├─ merge後digest不一致
│  └─ 同一内容の別transaction／重複PR
└─ 不可逆性を伴う中断
   ├─ commit前 → local破棄可
   ├─ commit済み・push不明 → remote照合までlocal破棄不可
   ├─ PR作成済み → PRを保持して再開、またはPRを明示的に閉じてから破棄
   └─ activation中 → config rollback後にverifiedへ戻す
```

## Edge Case Categories

### Input Validation

| Scenario | Expected Behavior | Priority | Notes |
|---|---|---:|---|
| GitHub URLが空／credential付き／query付き | Git操作前に拒否 | P1 | local stateを増やさない |
| 選択folderにSKILL.mdがない | 登録前に拒否 | P1 | 部分importなし |
| catalog／INDEX／依存関係が不整合 | preview前に拒否 | P1 | 該当理由を表示 |
| remoteとの差分が0件 | PRを作らず「変更なし」と表示 | P1 | `gh pr create`へ進まない |

### Boundary Conditions

| Scenario | Expected Behavior | Priority | Notes |
|---|---|---:|---|
| prepare直前／直後に停止 | journalから同じpreviewを再構築 | P1 | 新transactionを作らない |
| commit直後／push直後に停止 | commitとremote headを照合して再利用 | P1 | 重複commit禁止 |
| PR作成直後／journal保存前に停止 | branchから既存PRを発見して再利用 | P1 | 重複PR禁止 |
| activation成功後／cleanup失敗 | activeを維持しcleanup_pendingを記録 | P1 | 成功を失敗へ降格しない |

### Error States

| Scenario | Expected Behavior | Priority | Notes |
|---|---|---:|---|
| PRがOPEN | 正常な`waiting_for_merge`。復旧ダイアログを出さない | P1 | PRを開く導線と再確認 |
| PRがCLOSED・未merge | `closed_unmerged`。再開または明示破棄を案内 | P1 | 自動で新PRを作らない |
| permission denied | 同一transactionでcleanup／再試行 | P1 | remote副作用の有無で破棄可否を変える |
| remote digest不一致 | activate禁止、証拠保持 | P1 | blind retryしない |
| GitHub認証失敗 | credential修復後の再試行を案内 | P2 | tokenをjournalへ保存しない |

### Concurrency

| Scenario | Expected Behavior | Priority | Notes |
|---|---|---:|---|
| 操作ボタン連打 | 実行中は無効化し1回だけ処理 | P1 | stage跨ぎのqueued click防止 |
| 同じlibrary／remoteで再度開始 | 最新の未完了transactionを再開 | P1 | 新branch／PR禁止 |
| 2画面から同じtransactionを操作 | journal／remoteを再読込し冪等処理 | P2 | 将来はOS lockも検討 |
| PR mergeと確認が同時 | state再取得後、merge commitを検証 | P1 | branch commitだけでactivateしない |

### Integration Failures

| Scenario | Expected Behavior | Priority | Notes |
|---|---|---:|---|
| GitHub unavailable／timeout | 状態保持、同一stage再試行 | P1 | local破棄を既定にしない |
| `gh pr view`が未知stateを返す | fail-closed、raw stateで判断しない | P1 | journal保持 |
| browserを開けない | PR URLを表示しcopy可能にする | P2 | transactionはpendingのまま |
| merge後remote clone失敗 | verifiedへ進めず再試行 | P1 | PRは既にmerge済みと明示 |

## Error Messages

| Error State | User Message | Additional Action |
|---|---|---|
| PR OPEN | 「PRは作成済みです。GitHubでマージ後、このボタンでもう一度確認してください。」 | `GitHubでPRを開く` |
| PR CLOSED | 「PRはマージされずに閉じられています。GitHubで再度開くか、この作業を明示的に終了してください。」 | PRを開く／状態保持 |
| Retryable before push | 「処理が途中で止まりました。保存状態から再試行できます。」 | 再試行／local破棄／保持 |
| Retryable after possible push | 「GitHubへ送信済みの可能性があります。状態を確認して再試行してください。」 | 再試行／保持。local-only破棄を出さない |
| Integrity failure | 「送信内容とGitHub上の内容が一致しないため停止しました。」 | 証拠表示／状態保持 |

## Recovery Paths

### PR merge待ち

**User sees:** PR URL、`waiting_for_merge`、GitHubでmerge後に再確認する説明。

**Recovery options:**

1. PRをブラウザで開いてreview／CI／mergeを完了する。
2. 画面を閉じ、後で同じtransactionを再開する。

**Data preservation:** local journal、branch、PR、commit、manifestをすべて保持する。新transactionは作らない。

### 実行障害

**User sees:** 失敗stage、remote副作用が確定／不明／なしのどれか、許可される復旧操作。

**Recovery options:**

1. 同じtransaction IDで再試行する。
2. remote副作用がない場合だけlocal作業を破棄する。
3. remote副作用がある、または不明な場合は状態を保持し、remote照合後に判断する。

**Data preservation:** journalを常に保持する。remote branch／PRを暗黙に削除しない。

### 重複transaction／PR

**User sees:** 既存transactionとPRを表示し、新規作成を停止する。

**Recovery options:**

1. 既存transactionを再開する。
2. 重複PRは対応journalを照合して明示的に閉じる。

**Data preservation:** 採用したtransactionのcommit／PRを保持し、孤立したlocal workspaceだけcleanupする。

## Test Scenarios

### Must Test (P1)

- [x] OPEN PR確認が例外にならず`waiting_for_merge`を返す。
- [x] OPEN PR確認で復旧／破棄ダイアログを表示しない。
- [x] 利用者がactivation確認を取消してもエラー表示しない。
- [x] 同一library／remoteの未完了transactionがあれば新規transaction／PRを作らない。
- [x] commit／push／PR各境界から同じcommit／PRを再利用する。
- [x] remote副作用がある可能性のある失敗ではlocal-only破棄を提示しない。
- [x] ボタン連打でstageを跨いだ二重操作が起きない。
- [x] PR CLOSED、merge済み、digest driftを別状態として扱う。

### Should Test (P2)

- [ ] browser起動失敗時もURLとjournalを保持する。
- [ ] GitHub認証／network失敗後に同一transactionで再試行できる。
- [ ] 2画面が同じjournalを読んでもcommitとPRが重複しない。

### Nice to Test (P3)

- [ ] 長時間CI待ち後の再開で最新PR stateを表示する。
- [ ] unknown GitHub stateを安全側で停止する。
