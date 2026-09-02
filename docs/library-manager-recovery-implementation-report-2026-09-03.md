# Library Manager 中断復旧 実装・対応報告

## 対応結果

Windowsのアクセス拒否を修正し、中断時に利用者が自力で復旧できる経路をGUIとCLIへ追加した。通常時の操作ボタンは従来どおり1個だけである。エラー時だけ選択ダイアログを表示し、次の3つから選べる。

- 保存済みjournalから復旧し、同じ段階を再試行する。
- ローカルの一時作業だけを破棄し、最初からやり直す。
- 状態を保持して画面へ戻る、または後で再開する。

アプリを終了しても、次回起動時に最新の未完了transactionを提示する。GUIが使えない場合は次のCLIを使える。

```powershell
python -m skill_magnet library recover --transaction-id TRANSACTION_ID
python -m skill_magnet library abandon --transaction-id TRANSACTION_ID --confirm
```

`abandon`はremote branchとPRを削除しない。外部状態を勝手に変更せず、利用者がGitHub上で判断できる状態を保つ。

## 中断段階ごとの動作

| 中断箇所 | 保存済み状態 | 復旧動作 |
|---|---|---|
| clone／preview作成中 | draft、remote、requested branch | 一時cloneを整理し、同じ入力からpreviewを再構築 |
| commit後／push前 | commit SHA、branch | workspaceを再利用し、同じcommitをpush |
| push後／PR記録前 | commit SHA、branch | remote branchを照合し、既存PRがあれば再利用 |
| PR作成後／merge確認中 | PR URL、commit、manifest | 固有verifierで再照合し、merge状態を再確認 |
| activation中 | 検証済みcommit | configをatomic更新し、失敗時は旧bytesへrollback |
| 完了後cleanup | active receipt | 完了を維持し、削除できなかった一時pathだけ記録 |

## remote保護

公開先cloneの全消去を廃止し、catalog、統合INDEX、各skillの`SKILL.md`と`acceptance.json`だけをoverlayする。README、LICENSE、監査、テスト、配布物など管理対象外のファイルは変更しない。将来の回帰で削除差分が発生しても、prepare時点で拒否してpushしない。

## 実トランザクションの復旧

- 危険な削除差分を含んでいたPR #1: `CLOSED`
- 既定branch: 未mergeのため無傷
- transaction `0d954338e09c4a97ad19639e69d5298a`: `published_pending`から`abandoned`へ移行
- local cleanup: 完了、`cleanup_pending: []`
- remote branch: 自動削除せず保持

## 検証

- Library Manager試験: 15件PASS
- 全回帰試験: 157件PASS、環境依存1件skip
- Python compileall: PASS
- Git差分整合: PASS
- 実データpreview smoke: アプリ管理libraryと`omusubiman5/codex-pmo-skills`を隔離cloneで合成し、変更35件、削除0件、既存`README.md`保持、既存`codex-pmo-orchestration/SKILL.md`保持、送信確認前で停止を確認
- smoke cleanup: 製品の`abandon`で`cleanup_pending: []`、GitHub書込み0件
