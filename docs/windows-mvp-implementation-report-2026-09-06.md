# Windows版MVP 実装・受入記録

状態：修正済み配布候補を出力・導入済み。終了操作の局所実機確認は合格。正式field・公開判定を継続中。

## 今回の出力

- 配布候補：`outputs/windows-mvp-a0e1639/skill_magnet-0.5.9-py3-none-any.whl`
- 最終fieldログ：`outputs/windows-mvp-a0e1639/field-result.log`
- 現在のcommit：`a0e16394806500e2a57f6265f71aaef939cb51fa`
- wheelは製品コードの候補 `bd49017` から生成。後続commitは試験・文書だけで、配布内容は同じ。
- 最後の実機失敗：`Library Manager remained visible after user-recoverable close; windows=Library Manager`。PID 22360の局所probeで、native EnumWindowsは可視の起動案内・終了案内（class `#32770`）を検出したが、UIA RootElement.Childrenは本体だけを返した。両案内はHWNDからのUIA取得では正しいPID・可視Windowとして取得できた。試験の検索漏れにより起動案内を閉じる前に本体へWM_CLOSEを送り、終了案内が重なっていた。WM_CLOSEが無視されたという仮説は棄却。試験側のみ、同PIDのnative可視Windowを検索へ併合し「起動案内OK→本体を一度だけ閉じる→終了案内OK」とする修正を検証中。
- 正式field成功証拠は未生成。release台帳更新、製品branchのpush・mergeは未完了。

終了修正の確認：fresh direct Manager PID 26376に修正済み `Close-LibraryManagerRecoverably` を実行し、起動案内OK→本体WM_CLOSE 1回→終了案内OK→プロセス終了を11.77秒で確認。libraryの前後SHA-256はともに `9779285d13e5bcc6b01ff3616ec7786f37078154001e11e49aae45c391c8fb7a`。ログ：`%TEMP%/skill-magnet-close-helper-fresh/{stdout,stderr}.log`。これは実際の試験関数による局所実機結果で、全Explorer経路の成功とは区別する。

候補 `b5ee53c` の正式fieldはManager終了・背景busy・閉鎖後再起動を通過し、8番目の選択フォルダー起動（PID 20544）で確認画面検索がtimeoutした。成功bundleは作成されていない。ログ：`%TEMP%/skill-magnet-field-b5ee53c/field.stdout-stderr.log`。同操作のdirect CLI局所確認（PID 33228）は約1秒で画面を取得し終了も成功、stdout/stderrは空。これだけで製品正常または試験不良とは断定せず、Explorer経由の再起動との差を調査する。

実Explorerの局所再起動確認（12.38秒）：同じ選択folderでPID 16504→閉鎖→PID 31956の新規起動が成功。両回とも確認画面・ownerのcontext_selectionを観測し、閉鎖後はPID/window/ownerが消滅。config/library/transactionsとfolder内容は前後不変。証跡：`%TEMP%/skill-magnet-explorer-selected-relaunch/result.json`。8番目timeoutはこの局所経路では再現しないため、製品コードは追加変更しない。

候補 `87e8f67` は前回の起動timeoutを通過し、空フォルダー登録で `Specific missing-SKILL.md recovery dialog did not appear`。direct登録でも先に「ローカルライブラリを読み取れません」が表示された。`finish_window_initialization` のcatalog_error分岐が `run_initial_registration` より先にreturnする実装と一致する。登録元の検査前に保管庫の移行・復旧が走り、無効な入力の原因を利用者に示せない製品側の順序問題と確定した。対策は登録元の読み取り検査だけを起動workerの先頭へ移し、無効なら保管庫の移行・読込・登録へ進めないこと。正常な登録元では既存の所有権・catalog検査を維持し、保護条件を迂回しない。

`c457c65` で上記順序を修正。実Tk subprocessの回帰1件（案内のmessageboxは記録用stub）でSKILL.mdエラー・設定済みURL・保管庫未作成を確認、2.574秒。wheel再生成・導入成功（SHA-256 `76abd3cc87aeef972dfbd591e9f4089436b865e116d60d9e272eb1ded0a8f635`、論理payload `a0b3e08cdb98ce1dcf5ef068a145272a258eba413d6b367420265b9561dac040`）。sourceと導入版library_ui.pyのSHA-256は `3222a4cbe07962dbb36d629f95746e4e24bfbf8c9bd7d72b04fc9dc58f13fbc0` で一致。standalone配布物の実dialog確認は別に実施する。

導入版の実案内ではSKILL.md不足と選び直し方法の表示が成功。閉鎖試験は本体と案内の同名だけでなく、取得対象そのものを誤っていた。PID 30856で旧helper返却が本体（HWND 57871158、TkTopLevel）と実測し、native MessageBox（HWND 1379742、class #32770）を再取得。同じ生存プロセスでOK→本体閉鎖→プロセス終了が2.32秒で成功した。案内検索を#32770へ限定し、閉鎖確認は当該HWNDだけを対象とした。証拠：`%TEMP%/skill-magnet-missing-dialog-identity/{identity,close-result}.json`。製品の追加変更はしていない。

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
