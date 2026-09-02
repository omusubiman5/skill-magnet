# 原因調査: Library ManagerのWinError 5

## 結論

画面に出た`[WinError 5] アクセスが拒否されました`は、remote検証用Git cloneを毎回同じ`remote-verifier`フォルダーへ作り、次の確認前に`shutil.rmtree`で即時削除していたことが直接原因である。Windows上ではGit objectのread-only属性、ウイルス対策ソフトやファイル索引の一時ハンドルにより、`.git/objects/pack/*.idx`の削除が短時間拒否されることがある。該当経路だけ、既存の属性解除付きcleanupを使っていなかった。

## 実データで確認した影響

- transaction: `0d954338e09c4a97ad19639e69d5298a`
- 失敗段階: PR作成後のremote merge確認
- 失敗対象: `remote-verifier/.git/objects/pack/*.idx`
- GitHub PR: `omusubiman5/codex-pmo-skills#1`

調査中、別の重大問題も確認した。従来の準備処理はclone先の`.git`以外を一度すべて削除してからlibraryをコピーしていた。このためPR #1にはREADME、監査資料、テスト結果、配布物など多数の削除が含まれていた。PRは未mergeだったため既定branchへの損傷はなかった。誤mergeを防ぐためPR #1を閉じ、ローカルtransactionを`abandoned`へ移行した。remote branchは暗黙に削除せず保持している。

## なぜ利用者が復旧できなかったか

journal自体は残っていたが、GUIには再開・破棄の操作がなく、例外ダイアログを閉じる以外の経路がなかった。また、remote確認が固定フォルダーの削除成功に依存していたため、同じボタンを押しても同じ場所で失敗した。

## 恒久対策

1. remote検証は毎回固有の`remote-verifier-<random>`を作り、古い検証cloneの削除成否を次工程の前提にしない。
2. cleanupはread-only解除と短い再試行を行う。残留しても正常なpublish／activateを失敗表示へ変えず、journalへ`cleanup_pending`を記録する。
3. 公開はcatalogで管理するファイルだけをoverlayする。remote既存ファイルを一括削除しない。
4. Git statusに削除が1件でもあれば、送信前にfail-closedで拒否する。
5. prepare開始時とcommit作成直後にjournal checkpointを保存する。push後の再試行は既存commit、branch、PRを再利用する。
6. GUI起動時に未完了transactionを検出し、再開、ローカル破棄、後で再開を利用者が選べるようにする。CLIにも`recover`と`abandon`を用意する。

## 再発防止試験

- Windowsアクセス拒否を1回注入し、属性解除・再試行後にcleanupが完了すること。
- remoteのREADMEと監査ファイルがpublish後も同一内容で残ること。
- 削除処理を故意に注入した場合、prepareが送信前に拒否すること。
- workspace消失後も同じtransaction IDで再構築でき、利用者の明示確認でローカル破棄できること。

