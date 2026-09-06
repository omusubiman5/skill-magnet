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

ネイティブ試験修正：不正manifest試験が自ら `Invoke` を呼んだ後、そのログをメニュー列挙の副作用と誤判定していた。列挙のみの無ログ確認をInvoke試験前へ移し、不正Invoke後は子プロセスが起動していないことを検査する。隔離した導入DLLで成功した。

回帰試験は旧Tk rootを弱参照で確認する。主スレッドGCなしの対照は参照残存で失敗し、製品修正ありは5プロセスすべてで参照消滅と背景GC成功を確認した。Manager部分をstubにした原因回帰試験であり、実Explorer受入は別途行う。

| 対象 | 結果 |
|---|---|
| `test_product_policy.py` | 10件成功 |
| `test_library_manager.py` | 57件成功、79.644秒 |
| transaction resilience／concurrency／library data safety／GitHub source | 36件成功、6.104秒 |
| Tk循環参照回収の回帰 | 修正なしは失敗、修正あり5プロセス成功（1テスト、3.955秒） |
| 利用者の実 `C:/Projects/cangjie-skill-clean/books` の検出 | 元データ変更なし。codex-cli 9、conflict-clarity 12、harness-bootstrap-prompt-v2-1 13、合計3パック34スキル |
| 差分の空白エラー | なし |

全体試験：414件、387.088秒。旧release台帳（187件・旧メニュー仕様）との不一致1件、スキップ1件。それ以外は成功。正式field成功後に台帳を実測更新し、この不一致を再検証する。

候補 `bd49017` のwheel SHA-256：`1441b5f59bce28c1d60a238a84b89ab5782600b4285696a3ae7a653ccfa39f40`。同一wheelを導入し、source／wheel／installedのcli.py SHA-256が一致。context-menu statusはusable、native bindingはすべて一致、隔離native contract試験は成功。

同候補の実Explorer試験ではManager遷移を通過した。次の復旧ダイアログで試験側クリックが拒否されたため診断を追加し、`initial=foreground` と確定した。試験がダイアログを前面化せずに「前面であること」を要求していた。対象を前面化・再取得してから既存の判定を行う最小修正を検証する。製品の配布内容はこの試験変更では変わらない。

単体テスト成功は実Explorer操作、実GitHub公開、実デスクトップ受け渡しの成功を意味しない。これらの実機結果、配布物、公開情報は確認後に追記する。

## 実GitHubの登録・更新・削除・再開

合成スキルだけを置く非公開保管庫 `omusubiman5/skill-magnet-mvp-smoke-20260906` を作成して確認した。ユーザーの既存保管庫・本番設定には変更を加えていない。試験コード・一時設定は `%TEMP%/skill-magnet-mvp-github-20260906` にある。

| 操作 | PR | 検証済みmerge commit |
|---|---|---|
| 登録・同一内容の再登録 | #1 | `2ad6bc6bb7b0c74bdb541f577734549e9ffb4f61` |
| 更新 | #2 | `b92b45855f94d994f68535137d369c44471dfb4c` |
| 削除 | #3 | `6c94b99499ab07caf18e54c41d73622f847b58a8` |

全3件で実GitHubへのpush・PR・merge・remote bytes検証・隔離設定へのactivateが成功した。publish後に新しいLibraryTransactionインスタンスで再開し、同一PRを再利用した。activateの再実行も同じreceiptを返した。PRは3件だけで全件MERGED、READMEは残存。これはManagerが使う処理の実外部操作試験であり、GUI上の全CRUDクリックを行った試験とは区別する。
