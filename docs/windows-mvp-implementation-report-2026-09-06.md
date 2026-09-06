# Windows版MVP 実装・受入記録

状態：作業中。公開完了を示す文書ではない。

## 出荷範囲

Windows Explorerからの起動、既存スキルの登録・更新・削除とGitHub反映、Codex Desktopアプリ／Claude Codeデスクトップアプリへの依頼受け渡し、エラー・中断からの復旧。

`policy/product-policy.json` に `release_profiles.windows_mvp` を追加。全OS版の完成条件は維持し、Windows限定の出荷区分を追加した。READMEとWindows CIのgate呼出を同区分へ対応した。既存の署名付きfield証拠・配布物照合は引き続き実行する。

## 起動障害の調査

- 引継ぎのfield失敗：候補 `0e5e581` でManagerの準備通知を12秒以内に取得できなかった。
- 直接起動の比較：WORKERの再現では3秒以内に準備通知が発行された。準備workerの恒常的停止という仮説は支持されない。
- 直接起動後の終了待ち：復旧案内のモーダルが存在し、閉じるとプロセスが終了した。これだけでは終了処理の停止とは判定できない。
- chooser→Managerの比較：実Tkのボタンから遷移し、両ownerが `library_manager` に到達した。準備workerは正常に返り、Tk callback例外もなかった。従ってtimeout追加の根拠はない。ログ：`%TEMP%/skill-magnet-chooser-manager-probe-0e5e581/chooser-manager-attempt2.log`。
- 実Explorer再試験：PID 18208 が準備通知前に停止。Windows Application Event 1000（2026-09-06 15:52:17 JST）のPID `0x4720` が一致し、`tcl86t.dll` の例外 `0x80000003` を確認した。field終了時のcleanupとは別に、アプリのクラッシュが存在する。クラッシュを引き起こす処理は調査中。ログ：`%TEMP%/skill-magnet-field-recheck-0e5e581/field.stdout-stderr.log`。
- GC比較試験：chooserから物理クリックで遷移した場合、背景workerでのGCに伴い古いTk変数の破棄が `main thread is not in main loop` を4件出した。直接Manager起動では同条件のGCが正常終了した。作成スレッド以外でのTkオブジェクト破棄を原因候補として、遷移前の主スレッドGCを比較検証する。ログ格納先：`%TEMP%/skill-magnet-gc-physical-probe`。
- 修正案の比較：chooser復帰後、Manager呼出前に主スレッドでGCを実行すると、分離した5プロセスすべてでManager生成・遅延worker GC・準備通知が成功した。Tk変数破棄の例外は消失した。既知のデータ復旧ダイアログを抑制した原因切り分け用probeであり、通常UI全体の受入とは区別する。ログ：`%TEMP%/skill-magnet-gc-physical-probe/chooser-mainthread-gc-five-runs-plain.log`。

## 実施済み検証

製品修正：`cli.py` のManager／登録画面への遷移直前に `gc.collect()` を追加し、破棄済みselectorのTk循環参照を作成スレッドで回収する。遷移後の背景スレッドへ解放を持ち越さない。

回帰試験は旧Tk rootを弱参照で確認する。主スレッドGCなしの対照は参照残存で失敗し、製品修正ありは5プロセスすべてで参照消滅と背景GC成功を確認した。Manager部分をstubにした原因回帰試験であり、実Explorer受入は別途行う。

| 対象 | 結果 |
|---|---|
| `test_product_policy.py` | 10件成功 |
| `test_library_manager.py` | 57件成功、79.644秒 |
| transaction resilience／concurrency／library data safety／GitHub source | 36件成功、6.104秒 |
| Tk循環参照回収の回帰 | 修正なしは失敗、修正あり5プロセス成功（1テスト、3.955秒） |
| 差分の空白エラー | なし |

単体テスト成功は実Explorer操作、実GitHub公開、実デスクトップ受け渡しの成功を意味しない。これらの実機結果、配布物、公開情報は確認後に追記する。
